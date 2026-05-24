"""
Bilibili RAG 知识库系统

ASR 服务 - 使用 DashScope 录音文件识别
"""
import asyncio
import json
import os
import shutil
import subprocess
import time
from http import HTTPStatus
from typing import Optional, Any
from urllib import request as urlrequest

import httpx
import dashscope
from dashscope.audio.asr import Transcription, Recognition
from dashscope.common.utils import default_headers, join_url
from dashscope.utils.oss_utils import OssUtils
from loguru import logger

from app.config import settings
from app.services.asr_temp_file_manager import ASRTempFileManager


class ASRService:
    """音频转文字服务（DashScope）"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: Optional[int] = None,
    ):
        self.api_key = api_key or settings.openai_api_key
        self.base_url = base_url or getattr(settings, "dashscope_base_url", None)
        self.timeout = timeout or getattr(settings, "asr_timeout", 600)
        self.input_format = getattr(settings, "asr_input_format", "pcm")
        self.temp_file_manager = ASRTempFileManager()

        # Support comma-separated model lists for automatic fallback
        _raw_model = model or getattr(settings, "asr_model", "paraformer-v2")
        self.models: list[str] = [m.strip() for m in _raw_model.split(",") if m.strip()]
        self.model = self.models[0] if self.models else "paraformer-v2"

        _raw_local = getattr(settings, "asr_model_local", self.model)
        self.local_models: list[str] = [m.strip() for m in _raw_local.split(",") if m.strip()]
        self.local_model = self.local_models[0] if self.local_models else self.model

    def _configure(self) -> None:
        if not self.api_key:
            raise ValueError("未配置 DASHSCOPE API Key")
        dashscope.api_key = self.api_key
        if self.base_url:
            dashscope.base_http_api_url = self.base_url

    def _get_output_value(self, output: Any, key: str, default=None):
        if isinstance(output, dict):
            return output.get(key, default)
        return getattr(output, key, default)

    def _transcode_audio_to_pcm(self, file_path: str) -> Optional[str]:
        """转码为 16k s16le PCM，适配 Recognition"""
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            logger.info("未检测到 ffmpeg，无法转码为 PCM")
            return None
        base, _ext = os.path.splitext(file_path)
        pcm_path = base + ".pcm"
        cmd = [
            ffmpeg,
            "-y",
            "-i", file_path,
            "-f", "s16le",
            "-acodec", "pcm_s16le",
            "-ac", "1",
            "-ar", "16000",
            pcm_path,
        ]
        try:
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            if result.returncode != 0:
                err = (result.stderr or "").strip()
                logger.warning(f"转码 PCM 失败: {err[:200]}")
                return None
            return pcm_path
        except Exception as e:
            logger.warning(f"转码 PCM 异常: {e}")
            return None

    def _transcode_audio_to_wav(self, file_path: str) -> Optional[str]:
        """转码为 16k 单声道 WAV"""
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            logger.info("未检测到 ffmpeg，无法转码为 WAV")
            return None
        base, _ext = os.path.splitext(file_path)
        wav_path = base + ".wav"
        cmd = [
            ffmpeg,
            "-y",
            "-i", file_path,
            "-ac", "1",
            "-ar", "16000",
            "-vn",
            wav_path,
        ]
        try:
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            if result.returncode != 0:
                err = (result.stderr or "").strip()
                logger.warning(f"转码 WAV 失败: {err[:200]}")
                return None
            return wav_path
        except Exception as e:
            logger.warning(f"转码 WAV 异常: {e}")
            return None

    def _prepare_recognition_input(self, file_path: str) -> Optional[str]:
        """按输入格式准备 Recognition 文件"""
        fmt = (self.input_format or "pcm").lower()
        if fmt == "wav":
            return self._transcode_audio_to_wav(file_path)
        return self._transcode_audio_to_pcm(file_path)

    def _recognize_local_file(self, file_path: str, title: Optional[str] = None) -> Optional[str]:
        """使用 Recognition 直传本地音频，支持多模型自动 fallback"""
        self._configure()
        if not os.path.exists(file_path):
            logger.warning(f"ASR 本地文件不存在: {file_path}")
            return None

        input_path = self._prepare_recognition_input(file_path)
        if not input_path:
            return None

        for model in self.local_models:
            logger.info(
                f"ASR Recognition 使用模型: {model}, format={self.input_format or 'pcm'}"
            )
            try:
                recognizer = Recognition(
                    model=model,
                    callback=None,
                    format=(self.input_format or "pcm"),
                    sample_rate=16000,
                )
                result = recognizer.call(input_path)
                logger.info(
                    "ASR Recognition 结果: status_code={}, code={}, message={}, request_id={}",
                    getattr(result, "status_code", None),
                    getattr(result, "code", None),
                    getattr(result, "message", None),
                    getattr(result, "request_id", None),
                )
                sentences = result.get_sentence() or []
                if isinstance(sentences, dict):
                    sentences = [sentences]
                texts = []
                for s in sentences:
                    if isinstance(s, dict):
                        t = s.get("text") or ""
                        if t:
                            texts.append(t)
                text = "\n".join(texts).strip() if texts else None
                if text:
                    preview = text[:120].replace("\n", " ").strip()
                    logger.info(f"ASR Recognition 成功，模型={model}，长度={len(text)}，预览：{preview}")
                    self.temp_file_manager.write_result(
                        title or os.path.splitext(os.path.basename(file_path))[0],
                        text,
                    )
                    return text
                logger.warning(f"ASR Recognition 模型 {model} 返回空结果，尝试下一个...")
            except Exception as e:
                logger.warning(f"ASR Recognition 模型 {model} 异常: {e}，尝试下一个...")

        logger.warning("ASR Recognition 所有模型均失败")
        return None

    def _download_transcription(self, url: str, title: Optional[str] = None) -> Optional[str]:
        try:
            raw = urlrequest.urlopen(url).read().decode("utf-8")
            data = json.loads(raw)
        except Exception as e:
            logger.warning(f"ASR 结果下载失败: {e}")
            return None

        texts = []
        transcripts = data.get("transcripts") or []
        for item in transcripts:
            text = item.get("text", "") or ""
            if text:
                texts.append(text)
                continue
            for s in item.get("sentences", []) or []:
                s_text = s.get("text", "") or ""
                if s_text:
                    texts.append(s_text)

        if not texts and isinstance(data.get("text"), str):
            texts.append(data["text"])

        text = "\n".join(texts).strip() if texts else None
        if text:
            self.temp_file_manager.write_result(title or "dashscope_asr", text)
        return text

    def _build_api_url(self, *parts: str) -> str:
        base_url = self.base_url or getattr(dashscope, "base_http_api_url", None)
        if not base_url:
            base_url = "https://dashscope.aliyuncs.com/api/v1"
        return join_url(base_url, *parts)

    def _submit_transcription_task_restful(self, audio_url: str, model: str) -> Optional[str]:
        url = self._build_api_url("services", "audio", "asr", "transcription")
        headers = {
            **default_headers(self.api_key),
            "Content-Type": "application/json",
            "X-DashScope-Async": "enable",
        }
        parameters = {}
        if "paraformer" in model:
            parameters["language_hints"] = ["zh", "en"]
        payload = {"model": model, "input": {"file_urls": [audio_url]}}
        if parameters:
            payload["parameters"] = parameters

        try:
            resp = httpx.post(url, json=payload, headers=headers, timeout=30.0)
        except Exception as e:
            logger.warning(f"ASR RESTful 提交失败: {e}")
            return None

        if resp.status_code != HTTPStatus.OK:
            logger.warning(f"ASR RESTful 提交失败: status_code={resp.status_code}, body={resp.text[:300]}")
            return None

        data = resp.json()
        task_id = data.get("task_id")
        if not task_id:
            output = data.get("output") if isinstance(data, dict) else None
            if isinstance(output, dict):
                task_id = output.get("task_id")
        return task_id

    def _fetch_transcription_task_restful(self, task_id: str) -> Optional[dict]:
        url = self._build_api_url("tasks", task_id)
        headers = default_headers(self.api_key)
        try:
            resp = httpx.get(url, headers=headers, timeout=30.0)
        except Exception as e:
            logger.warning(f"ASR RESTful 查询失败: {e}")
            return None

        if resp.status_code != HTTPStatus.OK:
            logger.warning(f"ASR RESTful 查询失败: status_code={resp.status_code}, body={resp.text[:300]}")
            return None

        data = resp.json()
        if isinstance(data, dict) and isinstance(data.get("output"), dict):
            return data["output"]
        return data if isinstance(data, dict) else None

    def _transcribe_sync_restful(self, audio_url: str, model: str, title: Optional[str] = None) -> Optional[str]:
        self._configure()
        task_id = self._submit_transcription_task_restful(audio_url, model)
        if not task_id:
            logger.warning(f"[ASR] 模型 {model} RESTful 未返回 task_id")
            return None
        logger.info(f"[ASR] 模型 {model} 任务已提交(RESTful): task_id={task_id}")

        start = time.time()
        output = None
        while True:
            if time.time() - start > self.timeout:
                logger.warning(f"[ASR] 模型 {model} 任务超时(RESTful)")
                return None
            output = self._fetch_transcription_task_restful(task_id)
            if not output:
                time.sleep(1.5)
                continue
            status = self._get_output_value(output, "task_status")
            if status in ("SUCCEEDED", "FAILED"):
                break
            time.sleep(1.5)

        task_status = self._get_output_value(output, "task_status")
        status_message = self._get_output_value(output, "status_message") or ""
        results = self._get_output_value(output, "results", []) or []
        logger.info(
            "[ASR] 模型 {} 任务状态(RESTful): task_id={}, task_status={}, status_message={}, results={}",
            model, task_id, task_status, status_message, len(results),
        )

        if task_status == "FAILED":
            logger.warning(f"[ASR] 模型 {model} 任务失败(RESTful): {status_message}")
            return None

        for item in results:
            sub_status = item.get("subtask_status")
            transcription_url = item.get("transcription_url")
            error_message = item.get("error_message") or item.get("message")
            if sub_status:
                logger.info(
                    "[ASR] 模型 {} 子任务(RESTful): subtask_status={}, has_url={}, error={}",
                    model, sub_status, bool(transcription_url), error_message,
                )
            if sub_status == "SUCCEEDED" and transcription_url:
                return self._download_transcription(transcription_url, title=title)

        logger.warning(f"[ASR] 模型 {model} 未返回有效转写结果(RESTful)")
        return None

    def _transcribe_sync(self, audio_url: str, title: Optional[str] = None) -> Optional[str]:
        self._configure()
        for model in self.models:
            result = self._try_transcribe_with_model(audio_url, model, title)
            if result:
                return result
            logger.warning(f"[ASR] 模型 {model} 转写失败或无结果，尝试下一个...")
        logger.warning("[ASR] 所有模型均失败")
        return None

    def _try_transcribe_with_model(self, audio_url: str, model: str, title: Optional[str] = None) -> Optional[str]:
        """使用指定单个模型转写，失败返回 None"""
        if audio_url.startswith("oss://"):
            return self._transcribe_sync_restful(audio_url, model, title=title)

        kwargs = {}
        if "paraformer" in model:
            kwargs["language_hints"] = ["zh", "en"]

        try:
            resp = Transcription.async_call(
                model=model,
                file_urls=[audio_url],
                **kwargs,
            )
        except Exception as e:
            logger.warning(f"[ASR] 模型 {model} 提交失败: {e}")
            return None

        output = getattr(resp, "output", None)
        task_id = self._get_output_value(output, "task_id")
        if not task_id:
            logger.warning(f"[ASR] 模型 {model} 未返回 task_id")
            return None
        logger.info(f"[ASR] 模型 {model} 任务已提交: task_id={task_id}")

        start = time.time()
        while True:
            status = self._get_output_value(output, "task_status")
            if status in ("SUCCEEDED", "FAILED"):
                break
            if time.time() - start > self.timeout:
                logger.warning(f"[ASR] 模型 {model} 任务超时")
                return None
            time.sleep(1.5)
            resp = Transcription.fetch(task=task_id)
            output = getattr(resp, "output", None)

        status_code = getattr(resp, "status_code", None)
        task_status = self._get_output_value(output, "task_status")
        status_message = self._get_output_value(output, "status_message") or ""
        logger.info(
            "[ASR] 模型 {} 任务状态: task_id={}, task_status={}, status_code={}, status_message={}",
            model, task_id, task_status, status_code, status_message,
        )

        if task_status == "FAILED":
            logger.warning(f"[ASR] 模型 {model} 任务失败: {status_message}")
            return None

        if status_code != HTTPStatus.OK:
            logger.warning(f"[ASR] 模型 {model} 请求失败: status_code={status_code}")
            return None

        results = self._get_output_value(output, "results", []) or []
        for item in results:
            sub_status = item.get("subtask_status")
            transcription_url = item.get("transcription_url")
            error_message = item.get("error_message") or item.get("message")
            if sub_status:
                logger.info(
                    "[ASR] 模型 {} 子任务: subtask_status={}, has_url={}, error={}",
                    model, sub_status, bool(transcription_url), error_message,
                )
            if sub_status == "SUCCEEDED" and transcription_url:
                return self._download_transcription(transcription_url, title=title)

        logger.warning(f"[ASR] 模型 {model} 未返回有效转写结果")
        return None

    def _upload_temp_file(self, file_path: str, model: Optional[str] = None) -> Optional[str]:
        """上传本地文件到 DashScope 临时 OSS，返回 oss:// URL"""
        self._configure()
        if not os.path.exists(file_path):
            logger.warning(f"ASR 本地文件不存在: {file_path}")
            return None
        try:
            upload_model = model or self.local_model or self.model
            oss_url = OssUtils.upload(
                model=upload_model,
                file_path=file_path,
                api_key=self.api_key,
            )
            logger.info(f"ASR 临时文件上传成功: {oss_url}")
            return oss_url
        except Exception as e:
            logger.warning(f"ASR 临时文件上传失败: {e}")
            return None

    async def transcribe_url(self, audio_url: str, title: Optional[str] = None) -> Optional[str]:
        return await asyncio.to_thread(self._transcribe_sync, audio_url, title)

    async def transcribe_local_file(self, file_path: str, title: Optional[str] = None) -> Optional[str]:
        """本地文件直传识别（Recognition）"""
        return await asyncio.to_thread(self._recognize_local_file, file_path, title)

    def _transcribe_sync_with_model(self, audio_url: str, model: str, title: Optional[str] = None) -> Optional[str]:
        """使用指定单个模型转写（用于本地文件上传后的直接调用）"""
        return self._try_transcribe_with_model(audio_url, model, title=title)
