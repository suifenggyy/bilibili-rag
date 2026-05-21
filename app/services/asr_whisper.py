"""
本地 ASR 服务 - 使用 openai-whisper 进行音频转写
"""
import asyncio
import os
import threading
import time
from types import SimpleNamespace
from typing import Optional

import httpx
from loguru import logger

from app.services.asr_temp_file_manager import ASRTempFileManager

try:
    import whisper
except ImportError:  # pragma: no cover - tested via runtime behavior instead
    whisper = SimpleNamespace(load_model=None)


class OpenAIWhisperASRService:
    """基于 openai-whisper 的本地音频转写服务。"""

    def __init__(
        self,
        model: str = "turbo",
        language: str = "zh",
        timeout: int = 600,
    ):
        self.model_name = model
        self.language = language
        self.timeout = timeout
        self._model = None
        self._model_lock = threading.Lock()
        self.temp_file_manager = ASRTempFileManager()

    async def transcribe_url(self, audio_url: str, title: Optional[str] = None) -> Optional[str]:
        tmp_path = await self._download_audio(audio_url, title=title)
        if not tmp_path:
            return None
        return await self.transcribe_local_file(tmp_path, title=title)

    async def transcribe_local_file(self, file_path: str, title: Optional[str] = None) -> Optional[str]:
        resolved_title = title or os.path.splitext(os.path.basename(file_path))[0]
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self._transcribe_file_sync, file_path, resolved_title),
                timeout=self.timeout,
            )
        except TimeoutError:
            logger.warning(f"[WhisperASR] 转写超时（>{self.timeout}s）: {file_path}")
            return None

    def _get_model(self):
        if self._model is not None:
            return self._model

        with self._model_lock:
            if self._model is not None:
                return self._model

            whisper_module = whisper
            if getattr(whisper_module, "load_model", None) is None:
                raise RuntimeError(
                    "未安装 openai-whisper，请先执行 `pip install -r requirements.txt`"
                )

            logger.info(f"[WhisperASR] 加载模型: {self.model_name}")
            self._model = whisper_module.load_model(self.model_name)
            return self._model

    def _transcribe_file_sync(self, file_path: str, title: str) -> Optional[str]:
        if not os.path.exists(file_path):
            logger.warning(f"[WhisperASR] 文件不存在: {file_path}")
            return None

        try:
            model = self._get_model()
            start = time.time()
            kwargs = {}
            if self.language:
                kwargs["language"] = self.language
            result = model.transcribe(file_path, **kwargs)
            elapsed = time.time() - start
            text = (result.get("text") or "").strip()
            if not text:
                logger.warning("[WhisperASR] 转写结果为空")
                return None

            preview = text[:120].replace("\n", " ")
            logger.info(
                f"[WhisperASR] 转写完成: model={self.model_name}, "
                f"耗时={elapsed:.1f}s, 长度={len(text)}, 预览: {preview}"
            )
            self.temp_file_manager.write_result(title, text)
            return text
        except Exception as e:
            logger.warning(f"[WhisperASR] 转写异常: {e}")
            return None

    async def _download_audio(self, audio_url: str, title: Optional[str] = None) -> Optional[str]:
        try:
            path_ext = os.path.splitext(httpx.URL(audio_url).path)[1] or ".m4s"
        except Exception:
            path_ext = ".m4s"
        tmp_path = self.temp_file_manager.build_path(title or "whisper_audio", path_ext, prefix="audio")

        try:
            async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
                async with client.stream("GET", audio_url) as resp:
                    if resp.status_code not in (200, 206):
                        logger.warning(
                            f"[WhisperASR] 音频下载失败: status={resp.status_code}"
                        )
                        return None
                    with open(tmp_path, "wb") as f:
                        async for chunk in resp.aiter_bytes():
                            if chunk:
                                f.write(chunk)
            if os.path.getsize(tmp_path) < 1024:
                logger.warning("[WhisperASR] 下载音频文件过小")
                return None
            self.temp_file_manager.cleanup_if_needed()
            return tmp_path
        except Exception as e:
            logger.warning(f"[WhisperASR] 音频下载异常: {e}")
            return None
