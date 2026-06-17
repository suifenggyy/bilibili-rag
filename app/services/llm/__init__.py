"""
Unified LLM factory layer.

Public API:
    get_llm_service(role)     → OpenAICompatibleLLMService  (async complete/stream)
    get_langchain_chat(role)  → ChatOpenAI                  (LangChain chains)
    get_openai_client(role)   → OpenAI                      (raw SDK)
    get_embeddings(role)      → Embeddings                  (vector embeddings)
    LLMMessage, LLMResponse, LLMRoleConfig, LLMProviderConfig (types)
"""

from app.services.llm.types import LLMMessage, LLMResponse, LLMRoleConfig, LLMProviderConfig
from app.services.llm.factory import (
    get_llm_service,
    get_langchain_chat,
    get_openai_client,
    get_embeddings,
    reset_factory,
)

__all__ = [
    "LLMMessage",
    "LLMResponse",
    "LLMRoleConfig",
    "LLMProviderConfig",
    "get_llm_service",
    "get_langchain_chat",
    "get_openai_client",
    "get_embeddings",
    "reset_factory",
]
