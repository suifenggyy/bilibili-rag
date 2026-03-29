"""
Bilibili RAG 知识库系统

本地 ASR 服务 - 使用 Ollama Whisper 进行音频转写
支持 Ollama v0.5+ 的 OpenAI 兼容 /v1/audio/transcriptions 接口

使用前需在本地安装并启动 Ollama，并拉取 Whisper 模型：
    ollama pull whisper
"""
import asyncio
import os
import shutil
import subprocess
import time
import tempfile
from typing import Optional

import httpx
from loguru import logger


class OllamaASRService:
    """
    基于 Ollama Whisper 的本地音频转写服务。

    兼容 ASRService 接口（transcribe_url / transcribe_local_file），
    可直接替换 ContentFetcher 中的 asr_service。
    """

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "whisper",
        language: str = "zh",
        timeout: int = 600,
    ):
        """
        Args:
            base_url: Ollama 服务地址，默认 http://localhost:11434
            model:    Whisper 模型名称，默认 whisper
                      可选：whisper（标准）、whisper:large（更高精度）
            language: 转写语言提示，默认 zh（中文），填 "" 则自动检测
            timeout:  请求超时秒数，默认 600
        """
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.language = language
        self.timeout = timeout

    # ==================== 公共接口 ====================

    async def transcribe_url(self, audio_url: str) -> Optional[str]:
        """
        下载音频 URL 后进行本地转写。

        Ollama Whisper 不支持直接传 URL，需先下载到本地再转写。
        """
        tmp_path = None
        try:
            tmp_path = await self._download_audio(audio_url)
            if not tmp_path:
                return None
            return await self.transcribe_local_file(tmp_path)
        finally:
            _remove_file(tmp_path)

    async def transcribe_local_file(self, file_path: str) -> Optional[str]:
        """将本地音频文件转写为文本"""
        return await asyncio.to_thread(self._transcribe_file_sync, file_path)

    # ==================== 内部实现 ====================

    def _transcribe_file_sync(self, file_path: str) -> Optional[str]:
        """同步转写本地文件（在线程中运行）"""
        if not os.path.exists(file_path):
            logger.warning(f"[OllamaASR] 文件不存在: {file_path}")
            return None

        # 转码为 16kHz 单声道 WAV（Whisper 的最佳输入格式）
        wav_path = self._to_wav(file_path)
        transcribe_path = wav_path or file_path
        cleanup_wav = wav_path and wav_path != file_path

        try:
            return self._call_ollama_transcribe(transcribe_path)
        finally:
            if cleanup_wav:
                _remove_file(wav_path)

    def _to_wav(self, file_path: str) -> Optional[str]:
        """使用 ffmpeg 将音频转为 16kHz 单声道 WAV"""
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            logger.info("[OllamaASR] 未检测到 ffmpeg，将直接使用原始音频文件")
            return None

        base, _ext = os.path.splitext(file_path)
        wav_path = base + "_ollama.wav"

        cmd = [
            ffmpeg, "-y",
            "-i", file_path,
            "-ac", "1",         # 单声道
            "-ar", "16000",     # 16kHz 采样率
            "-vn",              # 不包含视频流
            wav_path,
        ]
        try:
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            if result.returncode != 0:
                err = (result.stderr or "").strip()
                logger.warning(f"[OllamaASR] ffmpeg 转码失败: {err[:200]}")
                return None
            if not os.path.exists(wav_path) or os.path.getsize(wav_path) < 1024:
                logger.warning("[OllamaASR] ffmpeg 输出文件过小")
                _remove_file(wav_path)
                return None
            return wav_path
        except Exception as e:
            logger.warning(f"[OllamaASR] ffmpeg 转码异常: {e}")
            return None

    def _call_ollama_transcribe(self, file_path: str) -> Optional[str]:
        """调用 Ollama OpenAI 兼容的转写接口"""
        endpoint = f"{self.base_url}/v1/audio/transcriptions"
        file_size = os.path.getsize(file_path)
        logger.info(
            f"[OllamaASR] 开始转写: model={self.model}, file={file_path}, "
            f"size={file_size // 1024}KB, endpoint={endpoint}"
        )

        start = time.time()
        try:
            with open(file_path, "rb") as f:
                files = {"file": (os.path.basename(file_path), f, "audio/wav")}
                data = {"model": self.model}
                if self.language:
                    data["language"] = self.language

                response = httpx.post(
                    endpoint,
                    files=files,
                    data=data,
                    timeout=self.timeout,
                )

            elapsed = time.time() - start

            if response.status_code != 200:
                logger.warning(
                    f"[OllamaASR] 转写失败: status={response.status_code}, "
                    f"body={response.text[:300]}"
                )
                return None

            result = response.json()
            text = result.get("text", "").strip()

            if not text:
                logger.warning("[OllamaASR] 转写结果为空")
                return None

            preview = text[:120].replace("\n", " ")
            logger.info(
                f"[OllamaASR] 转写完成: 耗时={elapsed:.1f}s, "
                f"长度={len(text)}, 预览: {preview}"
            )
            return text

        except httpx.ConnectError:
            logger.error(
                f"[OllamaASR] 无法连接到 Ollama 服务 ({self.base_url})，"
                "请确认 Ollama 已启动（运行 `ollama serve`）"
            )
            return None
        except httpx.TimeoutException:
            elapsed = time.time() - start
            logger.warning(f"[OllamaASR] 转写超时（{elapsed:.0f}s > {self.timeout}s）")
            return None
        except Exception as e:
            logger.warning(f"[OllamaASR] 转写异常: {e}")
            return None

    async def _download_audio(self, audio_url: str) -> Optional[str]:
        """下载音频 URL 到临时文件"""
        tmp_dir = os.path.join("data", "asr_tmp")
        os.makedirs(tmp_dir, exist_ok=True)
        tmp_path = os.path.join(tmp_dir, f"ollama_{int(time.time())}.m4s")

        try:
            async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
                async with client.stream("GET", audio_url) as resp:
                    if resp.status_code not in (200, 206):
                        logger.warning(
                            f"[OllamaASR] 音频下载失败: status={resp.status_code}"
                        )
                        return None
                    with open(tmp_path, "wb") as f:
                        async for chunk in resp.aiter_bytes():
                            if chunk:
                                f.write(chunk)
            if os.path.getsize(tmp_path) < 1024:
                logger.warning("[OllamaASR] 下载音频文件过小")
                _remove_file(tmp_path)
                return None
            return tmp_path
        except Exception as e:
            logger.warning(f"[OllamaASR] 音频下载异常: {e}")
            _remove_file(tmp_path)
            return None

    # ==================== 工具方法 ====================

    def check_ollama_available(self) -> bool:
        """检查 Ollama 服务是否可用"""
        try:
            resp = httpx.get(f"{self.base_url}/api/tags", timeout=5.0)
            return resp.status_code == 200
        except Exception:
            return False

    def check_model_available(self) -> bool:
        """检查指定 Whisper 模型是否已在 Ollama 中安装"""
        try:
            resp = httpx.get(f"{self.base_url}/api/tags", timeout=5.0)
            if resp.status_code != 200:
                return False
            models = resp.json().get("models", [])
            model_names = [m.get("name", "").split(":")[0] for m in models]
            return self.model.split(":")[0] in model_names
        except Exception:
            return False


def _remove_file(path: Optional[str]) -> None:
    """安全删除文件"""
    if path and os.path.exists(path):
        try:
            os.remove(path)
        except Exception as e:
            logger.debug(f"[OllamaASR] 清理临时文件失败: {path} - {e}")
