"""
Bilibili RAG 知识库系统

核心配置模块
"""
from pydantic_settings import BaseSettings
from pydantic import Field, AliasChoices
from typing import Optional
import os
import importlib.util

# Load text_model_prompts directly to avoid triggering services/__init__.py (circular import)
_prompts_path = os.path.join(os.path.dirname(__file__), "services", "text_model_prompts.py")
_spec = importlib.util.spec_from_file_location("text_model_prompts", _prompts_path)
_prompts_mod = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(_prompts_mod)  # type: ignore[union-attr]
DEFAULT_TEXT_MODEL_CORRECTION_PROMPT: str = _prompts_mod.DEFAULT_TEXT_MODEL_CORRECTION_PROMPT
DEFAULT_TEXT_MODEL_SUMMARY_PROMPT: str = _prompts_mod.DEFAULT_TEXT_MODEL_SUMMARY_PROMPT

_knowledge_prompt_defaults_path = os.path.join(
    os.path.dirname(__file__),
    "services",
    "knowledge_pipeline",
    "prompt_defaults.py",
)
_knowledge_prompt_spec = importlib.util.spec_from_file_location(
    "knowledge_prompt_defaults",
    _knowledge_prompt_defaults_path,
)
_knowledge_prompt_mod = importlib.util.module_from_spec(_knowledge_prompt_spec)  # type: ignore[arg-type]
_knowledge_prompt_spec.loader.exec_module(_knowledge_prompt_mod)  # type: ignore[union-attr]

DEFAULT_TOPIC_PATH_PROMPT = _knowledge_prompt_mod.DEFAULT_TOPIC_PATH_PROMPT
DEFAULT_KNOWLEDGE_NOTE_DISTILL_PROMPT = _knowledge_prompt_mod.DEFAULT_KNOWLEDGE_NOTE_DISTILL_PROMPT
DEFAULT_TOPIC_SUMMARY_DECISION_PROMPT = _knowledge_prompt_mod.DEFAULT_TOPIC_SUMMARY_DECISION_PROMPT
DEFAULT_TOPIC_SUMMARY_PROMPT = _knowledge_prompt_mod.DEFAULT_TOPIC_SUMMARY_PROMPT
DEFAULT_TOPIC_DETAIL_PROMPT = _knowledge_prompt_mod.DEFAULT_TOPIC_DETAIL_PROMPT
DEFAULT_KNOWLEDGE_REPAIR_PROMPT = _knowledge_prompt_mod.DEFAULT_KNOWLEDGE_REPAIR_PROMPT
DEFAULT_TOPIC_DEDUP_PROMPT = _knowledge_prompt_mod.DEFAULT_TOPIC_DEDUP_PROMPT


class Settings(BaseSettings):
    """应用配置"""

    # ------------------------------------------------------------------
    # LLM 提供方注册表 & 角色配置
    # ------------------------------------------------------------------
    #
    # 提供方（Provider）：每个提供方只需配置一次 base_url 和 api_key。
    #   内置提供方通过现有字段自动映射：
    #     dashscope  → OPENAI_BASE_URL + DASHSCOPE_API_KEY
    #     ollama     → OLLAMA_BASE_URL/v1（无 key）
    #     localopenai → LOCAL_OPENAI_BASE_URL
    #     omlx       → OMLX_BASE_URL/v1 + OMLX_MODEL（无 key）
    #   自定义提供方通过环境变量注册：
    #     LLM_PROVIDER_DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
    #     LLM_PROVIDER_DEEPSEEK_API_KEY=sk-xxx
    #     LLM_PROVIDER_MINIMAX_BASE_URL=https://api.minimax.chat/v1
    #     LLM_PROVIDER_MINIMAX_API_KEY=xxx
    #
    # 角色（Role）：每个角色通过 PROVIDER:MODEL 格式指定提供方和模型。
    #   环境变量：LLM_<ROLE>_MODEL=PROVIDER:MODEL
    #   示例：
    #     LLM_KNOWLEDGE_DISTILL_MODEL=ollama:qwen3:35b
    #     LLM_CHAT_ROUTING_MODEL=dashscope:qwen3-mini
    #     LLM_CHAT_MODEL=deepseek:deepseek-chat
    #
    #   第一个冒号前是提供方名称，后面是模型名称（模型名可含冒号如 qwen3:35b）。
    #   不设置则回退到 _ROLE_DEFAULTS 中的默认值（零 .env 改动即可运行）。

    # 内置提供方 → 从哪些 Settings 字段取 base_url / api_key
    _PROVIDER_REGISTRY: dict = {
        "dashscope": {
            "base_url_field": "openai_base_url",
            "api_key_field": "openai_api_key",
        },
        "ollama": {
            "base_url_field": "ollama_base_url",
            "api_key_field": "",  # Ollama 无需 key
            "base_url_suffix": "/v1",  # 追加 /v1 走 OpenAI 兼容协议
        },
        "localopenai": {
            "base_url_field": "local_openai_base_url",
            "api_key_field": "openai_api_key",
        },
        "omlx": {
            "base_url_field": "omlx_base_url",
            "api_key_field": "",  # OMLX 本地推理无需 key
            "base_url_suffix": "/v1",  # 追加 /v1 走 OpenAI 兼容协议
        },
    }

    # 角色 → 默认 PROVIDER:MODEL + 超参
    # model_spec 中的 {field} 会从 Settings 同名字段取值
    _ROLE_DEFAULTS: dict = {
        "rag_qa": {
            "model_spec": "dashscope:{llm_model}",
            "temperature": 0.5,
            "timeout": 300,
            "max_tokens": 4096,
        },
        "rag_summarize": {
            "model_spec": "dashscope:{llm_model}",
            "temperature": 0.5,
            "timeout": 300,
            "max_tokens": 4096,
        },
        "chat": {
            "model_spec": "dashscope:{llm_model}",
            "temperature": 0.5,
            "timeout": 300,
            "max_tokens": 4096,
        },
        "chat_routing": {
            "model_spec": "dashscope:{llm_model}",
            "temperature": 0.0,
            "timeout": 30,
            "max_tokens": 64,
        },
        "knowledge_distill": {
            "model_spec": "localopenai:{local_openai_model}",
            "temperature": 0.3,
            "timeout": 300,
            "max_tokens": 4096,
        },
        "knowledge_topic_path": {
            "model_spec": "localopenai:{local_openai_model}",
            "temperature": 0.3,
            "timeout": 300,
            "max_tokens": 4096,
        },
        "knowledge_topic_summary": {
            "model_spec": "localopenai:{local_openai_model}",
            "temperature": 0.3,
            "timeout": 300,
            "max_tokens": 4096,
        },
        "knowledge_topic_detail": {
            "model_spec": "localopenai:{local_openai_model}",
            "temperature": 0.3,
            "timeout": 300,
            "max_tokens": 4096,
        },
        "knowledge_classify": {
            "model_spec": "localopenai:{local_openai_model}",
            "temperature": 0.1,
            "timeout": 120,
            "max_tokens": 4096,
        },
        "knowledge_topic_dedup": {
            "model_spec": "localopenai:{local_openai_model}",
            "temperature": 0.0,
            "timeout": 60,
            "max_tokens": 2048,
        },
        "asr_correction": {
            "model_spec": "localopenai:{local_openai_model}",
            "temperature": 0.3,
            "timeout": 300,
            "max_tokens": 4096,
            "prompt": "{text_model_correction_prompt}",
        },
        "content_summary": {
            "model_spec": "localopenai:{local_openai_model}",
            "temperature": 0.3,
            "timeout": 300,
            "max_tokens": 4096,
            "prompt": "{text_model_summary_prompt}",
        },
        "rag_embedding": {
            "model_spec": "dashscope:{embedding_model}",
            "temperature": 0.0,
            "timeout": 300,
            "max_tokens": 4096,
        },
    }

    def get_provider_config(self, provider: str) -> "LLMProviderConfig":
        """Resolve a provider's base_url and api_key.

        1. 内置提供方：从 _PROVIDER_REGISTRY 映射到 Settings 字段。
        2. 自定义提供方：从 LLM_PROVIDER_<NAME>_BASE_URL / _API_KEY 读取。
        """
        from app.services.llm.types import LLMProviderConfig

        builtin = self._PROVIDER_REGISTRY.get(provider)
        if builtin:
            base_url = getattr(self, builtin["base_url_field"], "")
            # 追加后缀（如 Ollama 的 /v1）
            suffix = builtin.get("base_url_suffix", "")
            if suffix and not base_url.rstrip("/").endswith(suffix):
                base_url = base_url.rstrip("/") + suffix
            api_key = ""
            if builtin.get("api_key_field"):
                api_key = getattr(self, builtin["api_key_field"], "")
            # 允许环境变量覆盖
            env_prefix = f"LLM_PROVIDER_{provider.upper()}"
            base_url = os.environ.get(f"{env_prefix}_BASE_URL", base_url)
            api_key = os.environ.get(f"{env_prefix}_API_KEY", api_key)
            return LLMProviderConfig(name=provider, base_url=base_url, api_key=api_key)

        # 自定义提供方：只从环境变量读取
        env_prefix = f"LLM_PROVIDER_{provider.upper()}"
        base_url = os.environ.get(f"{env_prefix}_BASE_URL", "")
        api_key = os.environ.get(f"{env_prefix}_API_KEY", "")
        return LLMProviderConfig(name=provider, base_url=base_url, api_key=api_key)

    def get_llm_config(self, role: str):
        """Resolve LLM configuration for a named role.

        Reads LLM_<ROLE>_MODEL=PROVIDER:MODEL env var first,
        then falls back to _ROLE_DEFAULTS.

        The provider's base_url and api_key are looked up from the provider registry.
        Additional per-role overrides: LLM_<ROLE>_TEMPERATURE, LLM_<ROLE>_TIMEOUT,
        LLM_<ROLE>_MAX_TOKENS.

        Returns an LLMRoleConfig instance.
        """
        from app.services.llm.types import LLMRoleConfig

        defaults = self._ROLE_DEFAULTS.get(role, self._ROLE_DEFAULTS["chat"])
        env_prefix = f"LLM_{role.upper()}"

        def _env(name: str, default: str = "") -> str:
            return os.environ.get(name, default)

        # 1. Resolve model spec: LLM_<ROLE>_MODEL or default
        model_spec = _env(f"{env_prefix}_MODEL") or self._expand_model_spec(defaults["model_spec"])

        # 2. Parse PROVIDER:MODEL  (first colon = provider; rest = model name)
        provider, model = self._parse_model_spec(model_spec)

        # 3. Look up provider config
        provider_config = self.get_provider_config(provider)

        # 4. Per-role overrides for temperature / timeout / max_tokens
        temperature = float(_env(f"{env_prefix}_TEMPERATURE", str(defaults["temperature"])))
        timeout = int(_env(f"{env_prefix}_TIMEOUT", str(defaults["timeout"])))
        max_tokens = int(_env(f"{env_prefix}_MAX_TOKENS", str(defaults["max_tokens"])))

        # 5. Resolve prompt (expand {field} placeholders from Settings)
        prompt = ""
        if "prompt" in defaults:
            prompt = self._expand_model_spec(defaults["prompt"])

        return LLMRoleConfig(
            provider=provider,
            base_url=provider_config.base_url,
            api_key=provider_config.api_key,
            model=model,
            temperature=temperature,
            timeout=timeout,
            max_tokens=max_tokens,
            prompt=prompt,
        )

    def _expand_model_spec(self, spec: str) -> str:
        """Expand {field} placeholders in model_spec from Settings fields.

        Example: "dashscope:{llm_model}" → "dashscope:qwen3-max"
        If the placeholder resolves to empty, returns just the provider name.
        """
        import re
        def _replace(m):
            field_name = m.group(1)
            value = getattr(self, field_name, "")
            return value
        result = re.sub(r"\{(\w+)\}", _replace, spec)
        # If model part is empty (e.g. "localopenai:"), strip trailing colon
        if result.endswith(":"):
            result = result[:-1]
        return result

    @staticmethod
    def _parse_model_spec(spec: str) -> tuple[str, str]:
        """Parse PROVIDER:MODEL spec.

        First colon separates provider from model name.
        Model names may contain colons (e.g. "ollama:qwen3:35b").

        Returns (provider, model).
        If no colon found, returns (spec, "") — spec is treated as provider only.
        """
        if ":" not in spec:
            return spec, ""
        parts = spec.split(":", 1)
        return parts[0], parts[1]

    # ------------------------------------------------------------------
    # OpenAI / LLM 配置
    # ------------------------------------------------------------------
    openai_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("DASHSCOPE_API_KEY", "OPENAI_API_KEY"),
    )
    openai_base_url: str = Field(default="https://api.openai.com/v1", env="OPENAI_BASE_URL")
    llm_model: str = Field(default="gpt-4-turbo", env="LLM_MODEL")
    embedding_model: str = Field(default="text-embedding-3-small", env="EMBEDDING_MODEL")

    # DashScope ASR
    asr_backend: str = Field(default="whisper", env="ASR_BACKEND")
    dashscope_base_url: str = Field(
        default="https://dashscope.aliyuncs.com/api/v1",
        env="DASHSCOPE_BASE_URL"
    )
    asr_model: str = Field(default="paraformer-v2", env="ASR_MODEL")
    asr_timeout: int = Field(default=600, env="ASR_TIMEOUT")
    asr_model_local: str = Field(default="paraformer-realtime-v2", env="ASR_MODEL_LOCAL")
    asr_input_format: str = Field(default="pcm", env="ASR_INPUT_FORMAT")
    # 并发处理时 DashScope 同时请求数（其他阶段不受此限制）
    asr_concurrency: int = Field(default=2, env="ASR_CONCURRENCY")

    # Ollama 本地 ASR（Whisper）
    ollama_base_url: str = Field(default="http://localhost:11434", env="OLLAMA_BASE_URL")
    ollama_asr_model: str = Field(default="whisper", env="OLLAMA_ASR_MODEL")
    ollama_asr_language: str = Field(default="zh", env="OLLAMA_ASR_LANGUAGE")
    # ASR 纠错 prompt（由 asr_correction 角色引用）
    text_model_correction_prompt: str = Field(
        default=DEFAULT_TEXT_MODEL_CORRECTION_PROMPT,
        env="TEXT_MODEL_CORRECTION_PROMPT",
    )
    # 正文总结 prompt（由 content_summary 角色引用）
    text_model_summary_prompt: str = Field(
        default=DEFAULT_TEXT_MODEL_SUMMARY_PROMPT,
        env="TEXT_MODEL_SUMMARY_PROMPT",
    )
    # ASR 纠错分块大小（字符数），超过此长度的文本将分段纠错
    text_model_correction_chunk_size: int = Field(
        default=4000,
        env="TEXT_MODEL_CORRECTION_CHUNK_SIZE",
    )

    # 本地 OpenAI 兼容接口（如 LM Studio / Jan / LocalAI，默认 http://localhost:4141）
    local_openai_base_url: str = Field(default="http://localhost:4141", env="LOCAL_OPENAI_BASE_URL")
    local_openai_model: str = Field(default="", env="LOCAL_OPENAI_MODEL")

    # OMLX 本地推理引擎（OpenAI 兼容协议）
    omlx_base_url: str = Field(default="http://localhost:1234", env="OMLX_BASE_URL")
    omlx_model: str = Field(default="Qwen3.5-9B-OptiQ-4bit", env="OMLX_MODEL")

    # 本地 openai-whisper ASR
    whisper_model: str = Field(default="small", env="WHISPER_MODEL")
    whisper_language: str = Field(default="zh", env="WHISPER_LANGUAGE")

    # 抖音导出配置
    douyin_cookie: str = Field(default="", env="DOUYIN_COOKIE")
    douyin_output_dir: str = Field(default="douyin_output", env="DOUYIN_OUTPUT_DIR")
    douyin_after_date: str = Field(default="", env="DOUYIN_AFTER_DATE")

    # 导出日期过滤（YYYY-MM-DD，留空则不限制）
    instapaper_after_date: str = Field(default="", env="INSTAPAPER_AFTER_DATE")
    youtube_after_date: str = Field(default="", env="YOUTUBE_AFTER_DATE")
    xiaoyuzhou_after_date: str = Field(default="", env="XIAOYUZHOU_AFTER_DATE")
    bilibili_after_date: str = Field(default="", env="BILIBILI_AFTER_DATE")

    # 抓取工作目录 / 最终导出目录
    content_workspace_root: str = Field(default="~/.bilibili-rag", env="CONTENT_WORKSPACE_ROOT")
    content_workspace_max_size_bytes: int = Field(
        default=1024 * 1024 * 1024,
        env="CONTENT_WORKSPACE_MAX_SIZE_BYTES",
    )
    content_workspace_retention_days: int = Field(default=3, env="CONTENT_WORKSPACE_RETENTION_DAYS")

    # Obsidian Vault 与知识库流水线配置
    obsidian_vault_root: str = Field(
        default="~/Obsidian/jarvis",
        env="OBSIDIAN_VAULT_ROOT",
    )
    obsidian_inbox_dir: str = Field(default="inbox", env="OBSIDIAN_INBOX_DIR")
    obsidian_knowledge_dir: str = Field(default="knowledge", env="OBSIDIAN_KNOWLEDGE_DIR")
    obsidian_topics_dir: str = Field(default="knowledge/_topics", env="OBSIDIAN_TOPICS_DIR")
    obsidian_daily_dir: str = Field(default="daily", env="OBSIDIAN_DAILY_DIR")
    obsidian_meta_dir: str = Field(default="_meta", env="OBSIDIAN_META_DIR")
    obsidian_local_rest_url: str = Field(
        default="http://127.0.0.1:27124",
        env="OBSIDIAN_LOCAL_REST_URL",
    )
    obsidian_local_rest_api_key: str = Field(default="", env="OBSIDIAN_LOCAL_REST_API_KEY")
    obsidian_write_backend: str = Field(default="obsidian_api", env="OBSIDIAN_WRITE_BACKEND")

    # Tavily API（用于日报外部信号搜索）
    tavily_api_key: str = Field(default="", env="TAVILY_API_KEY")

    # 知识库分类流水线配置
    knowledge_classification_timeout: int = Field(
        default=120,
        env="KNOWLEDGE_CLASSIFICATION_TIMEOUT",
    )

    # Hierarchical knowledge pipeline prompts
    knowledge_topic_path_prompt: str = Field(default=DEFAULT_TOPIC_PATH_PROMPT, env="KNOWLEDGE_TOPIC_PATH_PROMPT")
    knowledge_note_distill_prompt: str = Field(default=DEFAULT_KNOWLEDGE_NOTE_DISTILL_PROMPT, env="KNOWLEDGE_NOTE_DISTILL_PROMPT")
    knowledge_topic_summary_decision_prompt: str = Field(default=DEFAULT_TOPIC_SUMMARY_DECISION_PROMPT, env="KNOWLEDGE_TOPIC_SUMMARY_DECISION_PROMPT")
    knowledge_topic_summary_prompt: str = Field(default=DEFAULT_TOPIC_SUMMARY_PROMPT, env="KNOWLEDGE_TOPIC_SUMMARY_PROMPT")
    knowledge_topic_detail_prompt: str = Field(default=DEFAULT_TOPIC_DETAIL_PROMPT, env="KNOWLEDGE_TOPIC_DETAIL_PROMPT")
    knowledge_repair_prompt: str = Field(default=DEFAULT_KNOWLEDGE_REPAIR_PROMPT, env="KNOWLEDGE_REPAIR_PROMPT")
    knowledge_topic_dedup_prompt: str = Field(default=DEFAULT_TOPIC_DEDUP_PROMPT, env="KNOWLEDGE_TOPIC_DEDUP_PROMPT")
    knowledge_min_body_chars: int = Field(default=80, env="KNOWLEDGE_MIN_BODY_CHARS")

    # Instapaper 配置
    instapaper_consumer_key: str = Field(default="", env="INSTAPAPER_CONSUMER_KEY")
    instapaper_consumer_secret: str = Field(default="", env="INSTAPAPER_CONSUMER_SECRET")
    instapaper_email: str = Field(default="", env="INSTAPAPER_EMAIL")
    instapaper_password: str = Field(default="", env="INSTAPAPER_PASSWORD")
    
    # 应用配置
    app_host: str = Field(default="0.0.0.0", env="APP_HOST")
    app_port: int = Field(default=8000, env="APP_PORT")
    debug: bool = Field(default=True, env="DEBUG")
    
    # 数据库
    database_url: str = Field(
        default="sqlite+aiosqlite:///./data/bilibili_rag.db",
        env="DATABASE_URL"
    )
    
    # ChromaDB
    chroma_persist_directory: str = Field(
        default="./data/chroma_db",
        env="CHROMA_PERSIST_DIRECTORY"
    )
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


# 全局配置实例
settings = Settings()


def ensure_directories():
    """确保必要的目录存在"""
    dirs = [
        "data",
        settings.chroma_persist_directory,
        "logs",
        os.path.expanduser(settings.content_workspace_root),
    ]
    for d in dirs:
        os.makedirs(d, exist_ok=True)
