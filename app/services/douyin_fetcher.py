"""
抖音内容获取器

下载抖音视频音频并使用 ASR 进行语音转写，输出转写文本。
支持 DashScope 和 Ollama 两种 ASR 后端（与 B站模块共用）。
"""

import asyncio
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx
from loguru import logger


@dataclass
class DouyinVideoContent:
    """抖音视频内容（含转写文本）"""

    aweme_id: str
    title: str
    author: str = ""
    create_time: int = 0
    duration: int = 0       # 毫秒
    cover_url: str = ""
    share_url: str = ""
    content: str = ""
    content_source: str = "basic_info"  # "asr" | "basic_info"


class DouyinContentFetcher:
    """
    抖音内容获取器

    核心流程：
        1. 从 play_urls 下载视频（首个可用 URL）
        2. 使用 ffmpeg 提取音频（WAV 16kHz 单声道）
        3. 调用 ASR 服务进行语音转写
        4. 失败时降级返回仅含基本信息的内容

    兼容 ASRService（DashScope）和 OllamaASRService 两种后端。
    """

    # 单个视频下载超时（秒）
    DOWNLOAD_TIMEOUT = 120
    # 音频下载最大文件大小：500MB
    MAX_AUDIO_SIZE = 500 * 1024 * 1024

    def __init__(self, asr_service, tmp_dir: str = "data/douyin_tmp"):
        """
        Args:
            asr_service:  ASR 服务实例（ASRService 或 OllamaASRService）
            tmp_dir:      临时文件目录
        """
        self.asr = asr_service
        self.tmp_dir = tmp_dir
        os.makedirs(tmp_dir, exist_ok=True)

    async def fetch_content(self, video_info: dict) -> DouyinVideoContent:
        """
        获取视频内容（下载音频 → ASR 转写），失败时降级。

        Args:
            video_info: DouyinService.parse_video_info() 返回的标准字段 dict

        Returns:
            DouyinVideoContent
        """
        aweme_id = video_info.get("aweme_id", "")
        title = video_info.get("title", "") or aweme_id
        base = DouyinVideoContent(
            aweme_id=aweme_id,
            title=title,
            author=video_info.get("author", ""),
            create_time=video_info.get("create_time", 0),
            duration=video_info.get("duration", 0),
            cover_url=video_info.get("cover_url", ""),
            share_url=video_info.get("share_url", ""),
        )

        play_urls = video_info.get("play_urls") or []
        if not play_urls:
            logger.warning(f"[DouyinFetcher] 视频无播放 URL: {aweme_id}")
            return base

        if self.asr is None:
            logger.warning(f"[DouyinFetcher] 未配置 ASR 服务，仅保存基本信息: {aweme_id}")
            return base

        # 尝试每个播放 URL，直到成功
        for idx, url in enumerate(play_urls[:3]):
            tmp_video = os.path.join(self.tmp_dir, f"{aweme_id}_{int(time.time())}.mp4")
            try:
                logger.debug(f"[DouyinFetcher] 下载视频 [{idx+1}/{min(len(play_urls),3)}]: {url[:80]}")
                downloaded = await self._download_video(url, tmp_video)
                if not downloaded:
                    continue

                transcript = await self._extract_and_transcribe(tmp_video, aweme_id)
                if transcript:
                    base.content = transcript
                    base.content_source = "asr"
                    return base

            except Exception as e:
                logger.warning(f"[DouyinFetcher] 处理失败 [{aweme_id}] url={idx}: {e}")
            finally:
                _remove_file(tmp_video)

        logger.warning(f"[DouyinFetcher] ASR 失败，降级为基本信息: {aweme_id}")
        return base

    # ==================== 私有方法 ====================

    async def _download_video(self, url: str, dest_path: str) -> bool:
        """使用 httpx 流式下载视频到本地文件"""
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Referer": "https://www.douyin.com/",
        }
        try:
            async with httpx.AsyncClient(
                timeout=self.DOWNLOAD_TIMEOUT,
                follow_redirects=True,
                headers=headers,
            ) as client:
                async with client.stream("GET", url) as resp:
                    if resp.status_code not in (200, 206):
                        logger.warning(
                            f"[DouyinFetcher] 下载失败 status={resp.status_code}: {url[:60]}"
                        )
                        return False

                    total = 0
                    with open(dest_path, "wb") as f:
                        async for chunk in resp.aiter_bytes(chunk_size=65536):
                            if chunk:
                                total += len(chunk)
                                if total > self.MAX_AUDIO_SIZE:
                                    logger.warning(
                                        f"[DouyinFetcher] 文件超过 {self.MAX_AUDIO_SIZE // 1024 // 1024}MB 限制"
                                    )
                                    return False
                                f.write(chunk)

            size = os.path.getsize(dest_path) if os.path.exists(dest_path) else 0
            if size < 10 * 1024:  # < 10KB，可能是错误页面
                logger.warning(f"[DouyinFetcher] 下载文件过小 ({size}B)，可能是无效响应")
                return False

            logger.debug(f"[DouyinFetcher] 下载完成: {size // 1024}KB → {dest_path}")
            return True

        except Exception as e:
            logger.warning(f"[DouyinFetcher] 下载异常: {e}")
            return False

    async def _extract_and_transcribe(self, video_path: str, aweme_id: str) -> Optional[str]:
        """提取音频 → ASR 转写"""
        wav_path = os.path.join(self.tmp_dir, f"{aweme_id}_{int(time.time())}_audio.wav")
        try:
            converted = await asyncio.to_thread(self._to_wav, video_path, wav_path)
            if not converted:
                # 直接尝试原始文件
                transcript = await self.asr.transcribe_local_file(video_path)
            else:
                transcript = await self.asr.transcribe_local_file(wav_path)
            return transcript
        finally:
            _remove_file(wav_path)

    def _to_wav(self, src: str, dst: str) -> bool:
        """使用 ffmpeg 将视频转为 16kHz 单声道 WAV"""
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            logger.info("[DouyinFetcher] 未检测到 ffmpeg，跳过音频转换")
            return False

        cmd = [
            ffmpeg, "-y",
            "-i", src,
            "-vn",              # 丢弃视频流
            "-ac", "1",         # 单声道
            "-ar", "16000",     # 16kHz
            "-acodec", "pcm_s16le",
            dst,
        ]
        try:
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=120,
            )
            if result.returncode != 0:
                err = (result.stderr or "").strip()[-200:]
                logger.warning(f"[DouyinFetcher] ffmpeg 转码失败: {err}")
                return False
            size = os.path.getsize(dst) if os.path.exists(dst) else 0
            if size < 1024:
                logger.warning("[DouyinFetcher] ffmpeg 输出文件过小")
                _remove_file(dst)
                return False
            return True
        except subprocess.TimeoutExpired:
            logger.warning("[DouyinFetcher] ffmpeg 超时")
            _remove_file(dst)
            return False
        except Exception as e:
            logger.warning(f"[DouyinFetcher] ffmpeg 异常: {e}")
            return False


def _remove_file(path: Optional[str]) -> None:
    """安全删除文件"""
    if path and os.path.exists(path):
        try:
            os.remove(path)
        except Exception as e:
            logger.debug(f"[DouyinFetcher] 清理临时文件失败: {path} - {e}")
