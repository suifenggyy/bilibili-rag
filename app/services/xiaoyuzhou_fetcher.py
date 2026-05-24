"""
小宇宙播客内容获取器

下载播客音频（MP3）并使用 ASR 进行语音转写，输出转写文本。
与 DouyinContentFetcher 结构对齐，支持相同 ASR 后端。
"""
import asyncio
import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Optional

import httpx
from loguru import logger

from app.services.content_storage import ContentStorageManager
from app.services.content_summary import ContentSummaryService
from app.services.text_postprocessor import TextPostProcessor
from app.services.text_postprocessor_factory import create_text_postprocessor


@dataclass
class XiaoyuzhouEpisodeContent:
    """小宇宙播客单集内容（含转写文本）"""
    episode_id: str
    title: str
    podcast_title: str = ""
    audio_url: str = ""
    duration: int = 0           # 秒
    cover_url: str = ""
    description: str = ""
    content: str = ""
    content_source: str = "basic_info"  # "asr" | "basic_info"
    summary_block: str = ""
    asr_raw_text: str = ""  # 纠错前的原始 ASR 文本，供重试使用


class XiaoyuzhouContentFetcher:
    """
    小宇宙播客内容获取器

    核心流程：
        1. httpx 流式下载 MP3（从 enclosure URL 直链）
        2. ffmpeg 转为 WAV 16kHz 单声道
        3. ASR 转写
        4. 失败时降级为标题 + 节目描述
    """

    DOWNLOAD_TIMEOUT = 300      # 秒，长播客可能很大
    MAX_AUDIO_SIZE = 500 * 1024 * 1024  # 500MB

    def __init__(
        self,
        asr_service,
        tmp_dir: str = "data/xiaoyuzhou_tmp",
        text_postprocessor: Optional[TextPostProcessor] = None,
        summary_service: Optional[ContentSummaryService] = None,
        storage_manager: Optional[ContentStorageManager] = None,
        xyz_service=None,  # XiaoyuzhouService，用于获取官方字幕（可选）
    ):
        self.asr = asr_service
        self.tmp_dir = tmp_dir
        self.text_postprocessor = text_postprocessor or create_text_postprocessor()
        self.summary_service = summary_service or ContentSummaryService()
        self.storage_manager = storage_manager or ContentStorageManager()
        self.xyz_service = xyz_service  # 有则优先用官方字幕，省去 ASR
        if hasattr(self.asr, "temp_file_manager"):
            self.asr.temp_file_manager.source = "xiaoyuzhou"
            self.asr.temp_file_manager.storage_manager = self.storage_manager

    async def fetch_content(
        self, episode_info: dict, podcast_title: str = ""
    ) -> XiaoyuzhouEpisodeContent:
        """
        获取单集内容（下载 MP3 → ASR 转写），失败时降级。

        Args:
            episode_info: XiaoyuzhouService 返回的单集 dict
            podcast_title: 所属播客标题

        Returns:
            XiaoyuzhouEpisodeContent
        """
        episode_id = episode_info.get("episode_id", "")
        title = episode_info.get("title", "") or episode_id
        audio_url = episode_info.get("audio_url", "")

        base = XiaoyuzhouEpisodeContent(
            episode_id=episode_id,
            title=title,
            podcast_title=podcast_title,
            audio_url=audio_url,
            duration=episode_info.get("duration") or 0,
            cover_url=episode_info.get("cover_url", ""),
            description=episode_info.get("description", ""),
        )

        if not audio_url:
            logger.warning(f"[XiaoyuzhouFetcher] 单集无音频 URL: {episode_id}")
            base.content = self._build_basic_content(base)
            return base

        # 优先尝试官方字幕（有字幕则跳过 ASR）
        if self.xyz_service and episode_id:
            try:
                transcript_text = await self.xyz_service.get_transcript(episode_id)
                if transcript_text:
                    logger.info(f"[XiaoyuzhouFetcher] 使用官方字幕: {episode_id}")
                    base.content = transcript_text
                    base.content_source = "transcript"
                    base.summary_block = await self._summarize_content(episode_id, base.content)
                    return base
            except Exception as e:
                logger.debug(f"[XiaoyuzhouFetcher] 官方字幕获取失败，改用 ASR: {e}")

        if self.asr is None:
            logger.warning(f"[XiaoyuzhouFetcher] 未配置 ASR 服务，仅保存基本信息: {episode_id}")
            base.content = self._build_basic_content(base)
            return base

        # 使用播客标题+集数标题作为文件目录标识
        storage_title = f"{podcast_title}_{title}" if podcast_title else title
        tmp_audio = str(
            self.storage_manager.build_work_file_path("xiaoyuzhou", storage_title, "audio.mp3")
        )
        try:
            downloaded = await self._download_audio(audio_url, tmp_audio)
            if not downloaded:
                logger.warning(f"[XiaoyuzhouFetcher] 音频下载失败: {episode_id}")
                base.content = self._build_basic_content(base)
                return base

            self.storage_manager.cleanup_workspace_if_needed()

            transcript = await self._extract_and_transcribe(tmp_audio, episode_id, title)
            if transcript:
                self.storage_manager.write_work_text("xiaoyuzhou", storage_title, "asr_raw.txt", transcript.strip())
                raw_asr = transcript.strip()
                base.content = await self._postprocess_asr_text(episode_id, transcript, title=title)
                base.asr_raw_text = raw_asr
                self.storage_manager.write_work_text("xiaoyuzhou", storage_title, "asr_corrected.txt", base.content.strip())
                base.content_source = "asr"
                base.summary_block = await self._summarize_content(episode_id, base.content)
                return base

        except Exception as e:
            logger.warning(f"[XiaoyuzhouFetcher] 处理失败 [{episode_id}]: {e}")

        logger.warning(f"[XiaoyuzhouFetcher] ASR 失败，降级为基本信息: {episode_id}")
        base.content = self._build_basic_content(base)
        return base

    # ==================== 私有方法 ====================

    async def _download_audio(self, url: str, dest_path: str) -> bool:
        """使用 httpx 流式下载音频"""
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; PodcastBot/1.0)",
            "Accept": "audio/mpeg, audio/*, */*",
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
                            f"[XiaoyuzhouFetcher] 下载失败 status={resp.status_code}: {url[:80]}"
                        )
                        return False
                    total = 0
                    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                    with open(dest_path, "wb") as f:
                        async for chunk in resp.aiter_bytes(chunk_size=65536):
                            if chunk:
                                total += len(chunk)
                                if total > self.MAX_AUDIO_SIZE:
                                    logger.warning(
                                        f"[XiaoyuzhouFetcher] 文件超过 "
                                        f"{self.MAX_AUDIO_SIZE // 1024 // 1024}MB 限制"
                                    )
                                    return False
                                f.write(chunk)

            size = os.path.getsize(dest_path) if os.path.exists(dest_path) else 0
            if size < 10 * 1024:
                logger.warning(f"[XiaoyuzhouFetcher] 下载文件过小 ({size}B)，可能是无效响应")
                return False

            logger.debug(f"[XiaoyuzhouFetcher] 下载完成: {size // 1024}KB → {dest_path}")
            return True
        except Exception as e:
            logger.warning(f"[XiaoyuzhouFetcher] 下载异常: {e}")
            return False

    async def _extract_and_transcribe(
        self, audio_path: str, episode_id: str, title: str
    ) -> Optional[str]:
        storage_title = title
        wav_path = str(
            self.storage_manager.build_work_file_path("xiaoyuzhou", storage_title, "audio.wav")
        )
        converted = await asyncio.to_thread(self._to_wav, audio_path, wav_path)
        if not converted:
            return await self.asr.transcribe_local_file(audio_path, title=title or episode_id)
        self.storage_manager.cleanup_workspace_if_needed()
        return await self.asr.transcribe_local_file(wav_path, title=title or episode_id)

    async def _postprocess_asr_text(self, episode_id: str, text: str, title: Optional[str] = None) -> str:
        processor = getattr(self, "text_postprocessor", None)
        raw_text = (text or "").strip()
        if not processor or not raw_text:
            return raw_text
        try:
            processed_text = await processor.postprocess(raw_text, title=title)
        except Exception as e:
            logger.warning(f"[XiaoyuzhouFetcher] 文本后处理失败，回退原始文本 [{episode_id}]: {e}")
            return raw_text
        normalized = (processed_text or "").strip()
        if not normalized:
            return raw_text
        return normalized

    async def _summarize_content(self, episode_id: str, text: str) -> str:
        summary_service = getattr(self, "summary_service", None)
        cleaned = (text or "").strip()
        if not summary_service or not cleaned:
            return ""
        try:
            return await summary_service.summarize(cleaned)
        except Exception as e:
            logger.warning(f"[XiaoyuzhouFetcher] 内容总结失败，跳过 [{episode_id}]: {e}")
            return ""

    @staticmethod
    def _build_basic_content(base: XiaoyuzhouEpisodeContent) -> str:
        parts = [f"播客节目：{base.podcast_title}" if base.podcast_title else ""]
        parts.append(f"单集标题：{base.title}")
        if base.description:
            parts.append(f"节目描述：{base.description[:500]}")
        return "\n\n".join(p for p in parts if p)

    def _to_wav(self, src: str, dst: str) -> bool:
        """使用 ffmpeg 将音频转为 16kHz 单声道 WAV"""
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            logger.info("[XiaoyuzhouFetcher] 未检测到 ffmpeg，跳过音频转换")
            return False

        cmd = [
            ffmpeg, "-y",
            "-i", src,
            "-vn",
            "-ac", "1",
            "-ar", "16000",
            "-acodec", "pcm_s16le",
            dst,
        ]
        try:
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=300,
            )
            if result.returncode != 0:
                err = (result.stderr or "").strip()[-300:]
                logger.warning(f"[XiaoyuzhouFetcher] ffmpeg 转码失败: {err}")
                return False
            size = os.path.getsize(dst) if os.path.exists(dst) else 0
            if size < 1024:
                logger.warning("[XiaoyuzhouFetcher] ffmpeg 输出文件过小")
                _remove_file(dst)
                return False
            return True
        except subprocess.TimeoutExpired:
            logger.warning("[XiaoyuzhouFetcher] ffmpeg 超时")
            _remove_file(dst)
            return False
        except Exception as e:
            logger.warning(f"[XiaoyuzhouFetcher] ffmpeg 异常: {e}")
            return False


def _remove_file(path: Optional[str]) -> None:
    if path and os.path.exists(path):
        try:
            os.remove(path)
        except Exception as e:
            logger.debug(f"[XiaoyuzhouFetcher] 清理临时文件失败: {path} - {e}")
