"""
LLM factory — role-based service creation.

All LLM access should go through this factory. Each role (rag_qa, chat,
knowledge_distill, etc.) is independently configurable via the
PROVIDER:MODEL format:

    LLM_<ROLE>_MODEL=PROVIDER:MODEL

For example:
    LLM_KNOWLEDGE_DISTILL_MODEL=ollama:qwen3:35b
    LLM_CHAT_ROUTING_MODEL=dashscope:qwen3-mini
    LLM_CHAT_MODEL=deepseek:deepseek-chat

Providers are registered once with base_url and api_key.
Built-in providers (dashscope, ollama, localopenai) map to existing
settings fields. Custom providers use LLM_PROVIDER_<NAME>_BASE_URL / _API_KEY.

Usage:
    from app.services.llm.factory import get_llm_service, get_langchain_chat

    # Simple async completion
    llm = get_llm_service("chat")
    response = await llm.complete(messages)

    # LangChain chain
    chat = get_langchain_chat("rag_qa")
    chain = prompt | chat | StrOutputParser()

    # Embeddings
    embeddings = get_embeddings("rag_embedding")
"""

from __future__ import annotations

from loguru import logger

from app.config import settings
from app.services.llm.types import LLMRoleConfig
from app.services.llm.openai_compatible import OpenAICompatibleLLMService

# Singleton cache keyed by role name
_instances: dict[str, OpenAICompatibleLLMService] = {}


def get_llm_service(role: str) -> OpenAICompatibleLLMService:
    """Get or create an LLMService for the given role.

    Instances are cached per role — calling twice with the same role
    returns the same object.
    """
    if role not in _instances:
        config = settings.get_llm_config(role)
        _instances[role] = _create_service(config)
        logger.info(
            f"LLM service created for role '{role}': "
            f"provider={config.provider}, model={config.model}, "
            f"base_url={config.base_url}"
        )
    return _instances[role]


def get_langchain_chat(role: str):
    """Convenience: get a LangChain ChatOpenAI for the given role.

    Used by RAG service for prompt | llm | parser chains.
    """
    return get_llm_service(role).as_langchain_chat()


def get_openai_client(role: str):
    """Convenience: get a raw OpenAI SDK client for the given role.

    Used when direct sync/stream calls are needed.
    """
    return get_llm_service(role).sync_client


def get_embeddings(role: str = "rag_embedding"):
    """Get a LangChain Embeddings instance for the given role.

    Handles DashScope vs OpenAI embeddings selection automatically.
    """
    config = settings.get_llm_config(role)
    return _create_embeddings(config)


def reset_factory() -> None:
    """Clear all cached instances. Useful for testing."""
    _instances.clear()


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------


def _create_service(config: LLMRoleConfig) -> OpenAICompatibleLLMService:
    """Create an OpenAICompatibleLLMService from role config."""
    return OpenAICompatibleLLMService(
        base_url=config.base_url,
        api_key=config.api_key,
        model=config.model,
        temperature=config.temperature,
        timeout=config.timeout,
        max_tokens=config.max_tokens,
    )


def _create_embeddings(config: LLMRoleConfig):
    """Create a LangChain Embeddings instance based on config.

    DashScope embeddings use a different class than OpenAI embeddings.
    """
    # Detect DashScope by provider name or URL
    if config.provider == "dashscope" or "dashscope" in config.base_url.lower():
        try:
            from langchain_community.embeddings import DashScopeEmbeddings
            return DashScopeEmbeddings(
                dashscope_api_key=config.api_key,
                model=config.model,
            )
        except ImportError:
            logger.warning("DashScopeEmbeddings not available, falling back to OpenAIEmbeddings")

    from langchain_openai import OpenAIEmbeddings
    return OpenAIEmbeddings(
        api_key=config.api_key,
        base_url=config.base_url,
        model=config.model,
        check_embedding_ctx_length=False,
    )
