"""
YouTube 内容获取器

从 YouTube 下载音频并使用 ASR 进行语音转写，输出转写文本。
与 DouyinContentFetcher 结构对齐，支持相同 ASR 后端。
"""
import asyncio
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from loguru import logger

from app.services.content_storage import ContentStorageManager
from app.services.content_summary import ContentSummaryService
from app.services.text_postprocessor import TextPostProcessor
from app.services.text_postprocessor_factory import create_text_postprocessor
from app.services.youtube import YouTubeService


@dataclass
class YouTubeVideoContent:
    """YouTube 视频内容（含转写文本）"""
    video_id: str
    title: str
    url: str = ""
    channel: str = ""
    duration: int = 0           # 秒
    cover_url: str = ""
    description: str = ""
    content: str = ""
    content_source: str = "basic_info"  # "asr" | "basic_info"
    summary_block: str = ""


class YouTubeContentFetcher:
    """
    YouTube 内容获取器

    核心流程：
        1. yt-dlp 下载最佳音频流
        2. ffmpeg 转为 WAV 16kHz 单声道
        3. ASR 转写
        4. 失败时降级为标题 + 描述基本信息
    """

    MAX_AUDIO_SIZE = 500 * 1024 * 1024  # 500MB

    def __init__(
        self,
        asr_service,
        youtube_service: Optional[YouTubeService] = None,
        tmp_dir: str = "data/youtube_tmp",
        text_postprocessor: Optional[TextPostProcessor] = None,
        summary_service: Optional[ContentSummaryService] = None,
        storage_manager: Optional[ContentStorageManager] = None,
    ):
        self.asr = asr_service
        self.yt = youtube_service or YouTubeService()
        self.tmp_dir = tmp_dir
        self.text_postprocessor = text_postprocessor or create_text_postprocessor()
        self.summary_service = summary_service or ContentSummaryService()
        self.storage_manager = storage_manager or ContentStorageManager()
        if hasattr(self.asr, "temp_file_manager"):
            self.asr.temp_file_manager.source = "youtube"
            self.asr.temp_file_manager.storage_manager = self.storage_manager

    async def fetch_content(self, video_info: dict) -> YouTubeVideoContent:
        """
        获取视频内容（下载音频 → ASR 转写），失败时降级。

        Args:
            video_info: YouTubeService.extract_video_info() 或 extract_playlist_videos() 返回的 dict

        Returns:
            YouTubeVideoContent
        """
        video_id = video_info.get("video_id", "")
        url = video_info.get("url", "")
        title = video_info.get("title", "") or video_id
        base = YouTubeVideoContent(
            video_id=video_id,
            title=title,
            url=url,
            channel=video_info.get("channel", ""),
            duration=video_info.get("duration") or 0,
            cover_url=video_info.get("thumbnail", ""),
            description=video_info.get("description", ""),
        )

        if not url:
            logger.warning(f"[YouTubeFetcher] 视频无 URL: {video_id}")
            return base

        if self.asr is None:
            logger.warning(f"[YouTubeFetcher] 未配置 ASR 服务，仅保存基本信息: {video_id}")
            base.content = self._build_basic_content(base)
            return base

        audio_dest = str(
            self.storage_manager.build_work_file_path("youtube", title, "audio")
        )
        try:
            downloaded = await self.yt.download_audio(url, audio_dest)
            if not downloaded:
                logger.warning(f"[YouTubeFetcher] 音频下载失败: {video_id}")
                base.content = self._build_basic_content(base)
                return base

            # 找到实际下载的文件
            actual_audio = YouTubeService._find_downloaded_file(Path(audio_dest))
            if not actual_audio or not actual_audio.exists():
                logger.warning(f"[YouTubeFetcher] 找不到下载的音频文件: {audio_dest}")
                base.content = self._build_basic_content(base)
                return base

            self.storage_manager.cleanup_workspace_if_needed()

            transcript = await self._extract_and_transcribe(
                str(actual_audio), video_id, title
            )
            if transcript:
                self.storage_manager.write_work_text("youtube", title, "asr_raw.txt", transcript.strip())
                base.content = await self._postprocess_asr_text(video_id, transcript, title=title)
                self.storage_manager.write_work_text("youtube", title, "asr_corrected.txt", base.content.strip())
                base.content_source = "asr"
                base.summary_block = await self._summarize_content(video_id, base.content)
                return base

        except Exception as e:
            logger.warning(f"[YouTubeFetcher] 处理失败 [{video_id}]: {e}")

        logger.warning(f"[YouTubeFetcher] ASR 失败，降级为基本信息: {video_id}")
        base.content = self._build_basic_content(base)
        return base

    # ==================== 私有方法 ====================

    async def _extract_and_transcribe(
        self, audio_path: str, video_id: str, title: str
    ) -> Optional[str]:
        """提取音频 → ASR 转写"""
        wav_path = str(
            self.storage_manager.build_work_file_path("youtube", title, "audio.wav")
        )
        converted = await asyncio.to_thread(self._to_wav, audio_path, wav_path)
        if not converted:
            return await self.asr.transcribe_local_file(audio_path, title=title or video_id)
        self.storage_manager.cleanup_workspace_if_needed()
        return await self.asr.transcribe_local_file(wav_path, title=title or video_id)

    async def _postprocess_asr_text(self, video_id: str, text: str, title: Optional[str] = None) -> str:
        processor = getattr(self, "text_postprocessor", None)
        raw_text = (text or "").strip()
        if not processor or not raw_text:
            return raw_text
        try:
            processed_text = await processor.postprocess(raw_text, title=title)
        except Exception as e:
            logger.warning(f"[YouTubeFetcher] 文本后处理失败，回退原始文本 [{video_id}]: {e}")
            return raw_text
        normalized = (processed_text or "").strip()
        if not normalized:
            logger.warning(f"[YouTubeFetcher] 文本后处理为空，回退原始文本 [{video_id}]")
            return raw_text
        return normalized

    async def _summarize_content(self, video_id: str, text: str) -> str:
        summary_service = getattr(self, "summary_service", None)
        cleaned = (text or "").strip()
        if not summary_service or not cleaned:
            return ""
        try:
            return await summary_service.summarize(cleaned)
        except Exception as e:
            logger.warning(f"[YouTubeFetcher] 内容总结失败，跳过 [{video_id}]: {e}")
            return ""

    @staticmethod
    def _build_basic_content(base: YouTubeVideoContent) -> str:
        parts = [f"视频标题：{base.title}"]
        if base.channel:
            parts.append(f"频道：{base.channel}")
        if base.description:
            parts.append(f"视频描述：{base.description[:500]}")
        return "\n\n".join(parts)

    def _to_wav(self, src: str, dst: str) -> bool:
        """使用 ffmpeg 将音频转为 16kHz 单声道 WAV"""
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            logger.info("[YouTubeFetcher] 未检测到 ffmpeg，跳过音频转换")
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
                logger.warning(f"[YouTubeFetcher] ffmpeg 转码失败: {err}")
                return False
            size = os.path.getsize(dst) if os.path.exists(dst) else 0
            if size < 1024:
                logger.warning("[YouTubeFetcher] ffmpeg 输出文件过小")
                _remove_file(dst)
                return False
            return True
        except subprocess.TimeoutExpired:
            logger.warning("[YouTubeFetcher] ffmpeg 超时")
            _remove_file(dst)
            return False
        except Exception as e:
            logger.warning(f"[YouTubeFetcher] ffmpeg 异常: {e}")
            return False


def _remove_file(path: Optional[str]) -> None:
    if path and os.path.exists(path):
        try:
            os.remove(path)
        except Exception as e:
            logger.debug(f"[YouTubeFetcher] 清理临时文件失败: {path} - {e}")
