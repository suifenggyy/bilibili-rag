"""
OpenAI 兼容代理文本后处理服务。
"""
from typing import Optional

import httpx
from loguru import logger

from app.config import settings


class ProxyTextPostProcessor:
    """使用 OpenAI 兼容 chat completions 接口对文本做纠错或总结。"""

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

    async def postprocess(self, text: str) -> str:
        cleaned = (text or "").strip()
        if not cleaned:
            return ""

        endpoint = f"{self.base_url}/v1/chat/completions"

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                endpoint,
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": self.prompt_template},
                        {"role": "user", "content": cleaned},
                    ],
                },
            )

        if response.status_code != 200:
            raise RuntimeError(
                f"代理文本后处理失败: status={response.status_code}, body={response.text[:300]}"
            )

        payload = response.json()
        choices = payload.get("choices") or []
        message = choices[0].get("message", {}) if choices else {}
        result = (message.get("content") or "").strip()
        if not result:
            raise RuntimeError("代理文本后处理返回空结果")

        preview = result[:120].replace("\n", " ").strip()
        logger.info(
            "代理文本后处理完成: model={}, length={}, preview={}",
            self.model,
            len(result),
            preview,
        )
        return result
