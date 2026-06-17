"""
Unified LLM abstraction types.

Defines the core data structures and Protocol for the LLM factory layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import AsyncIterator, Protocol


@dataclass
class LLMMessage:
    """A single message in an LLM conversation."""

    role: str  # "system" | "user" | "assistant"
    content: str


@dataclass
class LLMResponse:
    """Response from an LLM completion call."""

    content: str
    model: str = ""
    usage: dict = field(default_factory=dict)


@dataclass
class LLMProviderConfig:
    """Configuration for an LLM provider (e.g. dashscope, ollama, deepseek)."""

    name: str = ""
    base_url: str = ""
    api_key: str = ""


@dataclass
class LLMRoleConfig:
    """Resolved configuration for a single LLM role.

    After parsing PROVIDER:MODEL and looking up the provider's base_url/api_key.
    """

    provider: str = ""      # provider name (e.g. "dashscope", "ollama")
    base_url: str = ""
    api_key: str = ""
    model: str = ""         # model name (e.g. "qwen3-max", "gemma4:e2b")
    temperature: float = 0.5
    timeout: int = 300
    max_tokens: int = 4096
    prompt: str = ""        # default system prompt for the role (expanded from _ROLE_DEFAULTS)


class LLMService(Protocol):
    """Unified LLM service interface.

    All LLM providers implement this protocol. The factory returns
    concrete implementations based on role configuration.
    """

    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout: int | None = None,
    ) -> LLMResponse:
        """Single-turn (or multi-turn) completion."""
        ...

    async def stream(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout: int | None = None,
    ) -> AsyncIterator[str]:
        """Streaming completion — yields content deltas."""
        ...
