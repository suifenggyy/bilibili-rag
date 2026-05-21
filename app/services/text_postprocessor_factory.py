from typing import Optional

from app.config import settings
from app.services.ollama_text_postprocessor import OllamaTextPostProcessor
from app.services.proxy_text_postprocessor import ProxyTextPostProcessor
from app.services.text_postprocessor import TextPostProcessor


def create_text_postprocessor(prompt_template: Optional[str] = None) -> TextPostProcessor:
    backend = (settings.text_model_backend or "ollama").strip().lower()
    kwargs = {
        "base_url": settings.text_model_base_url,
        "model": settings.text_model_name,
        "prompt_template": prompt_template or settings.text_model_correction_prompt,
        "timeout": settings.text_model_timeout,
    }
    if backend == "proxy":
        return ProxyTextPostProcessor(**kwargs)
    return OllamaTextPostProcessor(**kwargs)
