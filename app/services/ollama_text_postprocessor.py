"""
Ollama 文本后处理服务。
"""
from typing import Optional

import httpx
from loguru import logger

from app.config import settings


class OllamaTextPostProcessor:
    """使用 Ollama 模型对 ASR 文本做纠错和格式化。"""

    def __init__(
        self,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        prompt_template: Optional[str] = None,
        timeout: Optional[int] = None,
    ):
        self.base_url = (base_url or settings.text_model_base_url).rstrip("/")
        self.model = model or settings.text_model_name
        self.prompt_template = prompt_template or settings.text_model_correction_prompt
        self.timeout = timeout or settings.text_model_timeout

    async def postprocess(self, text: str, title: Optional[str] = None) -> str:
        """调用 Ollama /api/generate 纠错并格式化文本。"""
        cleaned = (text or "").strip()
        if not cleaned:
            return ""

        if title:
            content = f"标题：{title.strip()}\n正文：{cleaned}"
        else:
            content = cleaned
        prompt = f"{self.prompt_template}\n\n{content}"
        endpoint = f"{self.base_url}/api/generate"

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                endpoint,
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "stream": False,
                },
            )

        if response.status_code != 200:
            raise RuntimeError(
                f"Ollama 文本后处理失败: status={response.status_code}, body={response.text[:300]}"
            )

        payload = response.json()
        result = (payload.get("response") or "").strip()
        if not result:
            raise RuntimeError("Ollama 文本后处理返回空结果")

        preview = result[:120].replace("\n", " ").strip()
        logger.info(
            "ASR 文本后处理完成: model={}, length={}, preview={}",
            self.model,
            len(result),
            preview,
        )
        return result
