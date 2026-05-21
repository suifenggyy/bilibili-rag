"""
ASR 后端工厂
"""
from typing import Optional

from app.config import settings
from app.services.asr import ASRService
from app.services.asr_local import OllamaASRService
from app.services.asr_whisper import OpenAIWhisperASRService


def resolve_asr_backend(backend: Optional[str] = None) -> str:
    """解析最终使用的 ASR 后端。"""
    resolved = (backend or settings.asr_backend or "whisper").strip().lower()
    if resolved == "auto":
        default_backend = (settings.asr_backend or "whisper").strip().lower()
        resolved = "whisper" if default_backend == "auto" else default_backend

    if resolved not in {"dashscope", "ollama", "whisper"}:
        raise ValueError(f"不支持的 ASR_BACKEND: {resolved}")
    return resolved


def create_asr_service(
    backend: Optional[str] = None,
    *,
    ollama_base_url: Optional[str] = None,
    ollama_model: Optional[str] = None,
    ollama_language: Optional[str] = None,
    whisper_model: Optional[str] = None,
    whisper_language: Optional[str] = None,
):
    """根据配置或显式参数构建 ASR 服务实例。"""
    resolved_backend = resolve_asr_backend(backend)

    if resolved_backend == "dashscope":
        return ASRService()

    if resolved_backend == "ollama":
        return OllamaASRService(
            base_url=ollama_base_url or settings.ollama_base_url,
            model=ollama_model or settings.ollama_asr_model,
            language=ollama_language if ollama_language is not None else settings.ollama_asr_language,
            timeout=settings.asr_timeout,
        )

    return OpenAIWhisperASRService(
        model=whisper_model or settings.whisper_model,
        language=whisper_language if whisper_language is not None else settings.whisper_language,
        timeout=settings.asr_timeout,
    )
