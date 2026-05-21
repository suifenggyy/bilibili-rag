"""
Bilibili RAG 知识库系统

服务模块初始化
"""
from app.services.bilibili import BilibiliService
from app.services.content_fetcher import ContentFetcher
from app.services.asr import ASRService
from app.services.asr_temp_file_manager import ASRTempFileManager
from app.services.asr_factory import create_asr_service, resolve_asr_backend
from app.services.asr_local import OllamaASRService
from app.services.ollama_text_postprocessor import OllamaTextPostProcessor
from app.services.proxy_text_postprocessor import ProxyTextPostProcessor
from app.services.content_summary import ContentSummaryService
from app.services.asr_whisper import OpenAIWhisperASRService
from app.services.rag import RAGService
from app.services.text_postprocessor_factory import create_text_postprocessor
from app.services.wbi import wbi_signer

__all__ = [
    "BilibiliService",
    "ContentFetcher", 
    "ASRService",
    "ASRTempFileManager",
    "OllamaASRService",
    "OllamaTextPostProcessor",
    "ProxyTextPostProcessor",
    "ContentSummaryService",
    "OpenAIWhisperASRService",
    "create_asr_service",
    "create_text_postprocessor",
    "resolve_asr_backend",
    "RAGService",
    "wbi_signer"
]
