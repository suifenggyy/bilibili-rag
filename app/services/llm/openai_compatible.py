"""
OpenAI-compatible LLM service implementation.

A single implementation that handles all providers speaking the
OpenAI chat completions protocol (Ollama, DashScope compatible mode,
Minimax, DeepSeek, local OpenAI, etc.). The difference is only
base_url / api_key / model.
"""

from __future__ import annotations

import json as _json
from typing import AsyncIterator

import httpx
from loguru import logger
from openai import AsyncOpenAI, OpenAI

from app.services.llm.types import LLMMessage, LLMResponse


class OpenAICompatibleLLMService:
    """LLM service using OpenAI-compatible chat completions API.

    This single class handles all providers because they all speak
    the /v1/chat/completions protocol. Provider differences are
    captured in base_url, api_key, and model name.

    Provides bridge methods:
      - as_langchain_chat() → ChatOpenAI for LangChain chains
      - sync_client → raw OpenAI client for sync/stream calls
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        temperature: float = 0.5,
        timeout: int = 300,
        max_tokens: int = 4096,
    ):
        self._base_url = base_url
        self._api_key = api_key
        self.model = model
        self.default_temperature = temperature
        self.default_timeout = timeout
        self.default_max_tokens = max_tokens

        # Lazy-initialised clients
        self._async_client: AsyncOpenAI | None = None
        self._sync_client: OpenAI | None = None

        logger.debug(
            f"LLM service configured: base_url={base_url}, model={model}, "
            f"temperature={temperature}, timeout={timeout}"
        )

    # ------------------------------------------------------------------
    # Client accessors (lazy)
    # ------------------------------------------------------------------

    @property
    def async_client(self) -> AsyncOpenAI:
        if self._async_client is None:
            self._async_client = AsyncOpenAI(
                api_key=self._api_key,
                base_url=self._base_url,
                timeout=self.default_timeout,
            )
        return self._async_client

    @property
    def sync_client(self) -> OpenAI:
        if self._sync_client is None:
            self._sync_client = OpenAI(
                api_key=self._api_key,
                base_url=self._base_url,
                timeout=self.default_timeout,
            )
        return self._sync_client

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _should_use_responses_api(self) -> bool:
        """Return True if the model requires the /v1/responses API.

        Reasoning models (o1, o3, o4 series) and some newer GPT models
        (e.g. gpt-5.4-mini) are designed for the responses API and
        do not support chat.completions.
        """
        model_lower = self.model.lower()
        return any(
            model_lower.startswith(p) for p in ("o1", "o3", "o4", "gpt-5.4")
        )

    # ------------------------------------------------------------------
    # Core LLMService methods
    # ------------------------------------------------------------------

    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout: int | None = None,
    ) -> LLMResponse:
        """Async completion via /v1/chat/completions or /v1/responses."""
        dict_messages = [{"role": m.role, "content": m.content} for m in messages]
        try:
            if self._should_use_responses_api():
                # Responses API 的 max_output_tokens 包含 reasoning + 可见输出，
                # 无法像 chat completions 的 max_tokens 那样只限制可见输出。
                # 不传 max_output_tokens，让模型自行决定输出长度。
                create_kwargs: dict = {
                    "model": self.model,
                    "input": dict_messages,  # type: ignore[arg-type]
                    "timeout": timeout if timeout is not None else self.default_timeout,
                }
                if max_tokens is not None:
                    create_kwargs["max_output_tokens"] = max_tokens
                response = await self.async_client.responses.create(**create_kwargs)
                content = response.output_text or ""
                if not content:
                    logger.warning(
                        f"Responses API returned empty output_text for model={self.model}, "
                        f"status={response.status}, "
                        f"output_types={[o.type for o in (response.output or [])]}, "
                        f"max_output_tokens={expanded_max}, "
                        f"usage={response.usage}"
                    )
                usage = {}
                if response.usage:
                    usage = {
                        "prompt_tokens": response.usage.input_tokens,
                        "completion_tokens": response.usage.output_tokens,
                        "total_tokens": response.usage.total_tokens,
                    }
                return LLMResponse(content=content, model=response.model, usage=usage)

            response = await self.async_client.chat.completions.create(
                model=self.model,
                messages=dict_messages,  # type: ignore[arg-type]
                temperature=temperature if temperature is not None else self.default_temperature,
                max_tokens=max_tokens if max_tokens is not None else self.default_max_tokens,
                timeout=timeout if timeout is not None else self.default_timeout,
            )
            choice = response.choices[0]
            content = choice.message.content or ""
            usage = {}
            if response.usage:
                usage = {
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                    "total_tokens": response.usage.total_tokens,
                }
            return LLMResponse(content=content, model=response.model, usage=usage)
        except Exception as e:
            logger.error(f"LLM complete failed (model={self.model}): {e}")
            raise

    async def stream(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout: int | None = None,
    ) -> AsyncIterator[str]:
        """Async streaming completion — yields content deltas."""
        dict_messages = [{"role": m.role, "content": m.content} for m in messages]
        try:
            if self._should_use_responses_api():
                stream = await self.async_client.responses.create(
                    model=self.model,
                    input=dict_messages,  # type: ignore[arg-type]
                    max_output_tokens=max_tokens if max_tokens is not None else self.default_max_tokens,
                    timeout=timeout if timeout is not None else self.default_timeout,
                    stream=True,
                )
                async for event in stream:
                    if event.type == "response.output_text.delta":
                        yield event.delta
                return

            stream = await self.async_client.chat.completions.create(
                model=self.model,
                messages=dict_messages,  # type: ignore[arg-type]
                temperature=temperature if temperature is not None else self.default_temperature,
                max_tokens=max_tokens if max_tokens is not None else self.default_max_tokens,
                timeout=timeout if timeout is not None else self.default_timeout,
                stream=True,
            )
            async for chunk in stream:
                delta = chunk.choices[0].delta
                if delta and delta.content:
                    yield delta.content
        except Exception as e:
            logger.error(f"LLM stream failed (model={self.model}): {e}")
            raise

    # ------------------------------------------------------------------
    # Bridge methods for legacy code
    # ------------------------------------------------------------------

    def as_langchain_chat(self):
        """Return a LangChain ChatOpenAI instance with the same config.

        Used by RAG service for prompt | llm | parser chains.
        """
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            api_key=self._api_key,
            base_url=self._base_url,
            model=self.model,
            temperature=self.default_temperature,
            max_tokens=self.default_max_tokens,
            timeout=self.default_timeout,
        )
