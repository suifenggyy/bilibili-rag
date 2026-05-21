"""
Bilibili RAG 知识库系统

核心配置模块
"""
from pydantic_settings import BaseSettings
from pydantic import Field, AliasChoices
from typing import Optional
import os

from app.services.text_model_prompts import (
    DEFAULT_TEXT_MODEL_CORRECTION_PROMPT,
    DEFAULT_TEXT_MODEL_SUMMARY_PROMPT,
)


class Settings(BaseSettings):
    """应用配置"""
    
    # OpenAI / LLM 配置
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

    # Ollama 本地 ASR（Whisper）
    ollama_base_url: str = Field(default="http://localhost:11434", env="OLLAMA_BASE_URL")
    ollama_asr_model: str = Field(default="whisper", env="OLLAMA_ASR_MODEL")
    ollama_asr_language: str = Field(default="zh", env="OLLAMA_ASR_LANGUAGE")
    text_model_backend: str = Field(default="ollama", env="TEXT_MODEL_BACKEND")
    text_model_base_url: str = Field(default="http://localhost:11434", env="TEXT_MODEL_BASE_URL")
    text_model_name: str = Field(default="gemma4:e2b", env="TEXT_MODEL_NAME")
    text_model_correction_prompt: str = Field(
        default=DEFAULT_TEXT_MODEL_CORRECTION_PROMPT,
        env="TEXT_MODEL_CORRECTION_PROMPT",
    )
    text_model_summary_prompt: str = Field(
        default=DEFAULT_TEXT_MODEL_SUMMARY_PROMPT,
        env="TEXT_MODEL_SUMMARY_PROMPT",
    )
    text_model_timeout: int = Field(default=300, env="TEXT_MODEL_TIMEOUT")

    # 本地 openai-whisper ASR
    whisper_model: str = Field(default="turbo", env="WHISPER_MODEL")
    whisper_language: str = Field(default="zh", env="WHISPER_LANGUAGE")

    # 抖音导出配置
    douyin_cookie: str = Field(default="", env="DOUYIN_COOKIE")
    douyin_evil0ctal_url: str = Field(default="http://localhost:2333", env="DOUYIN_EVIL0CTAL_URL")
    douyin_output_dir: str = Field(default="douyin_output", env="DOUYIN_OUTPUT_DIR")

    # 抓取工作目录 / 最终导出目录
    content_workspace_root: str = Field(default="~/.bilibili-rag", env="CONTENT_WORKSPACE_ROOT")
    content_workspace_max_size_bytes: int = Field(
        default=1024 * 1024 * 1024,
        env="CONTENT_WORKSPACE_MAX_SIZE_BYTES",
    )
    content_workspace_retention_days: int = Field(default=3, env="CONTENT_WORKSPACE_RETENTION_DAYS")
    collection_output_dir: str = Field(
        default="/Users/gongyongyue/FangcloudV2/personal_space.localized/同步空间/个人资料/Obsidian/jarvis/collection",
        env="COLLECTION_OUTPUT_DIR",
    )

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
        os.path.expanduser(settings.collection_output_dir),
    ]
    for d in dirs:
        os.makedirs(d, exist_ok=True)
