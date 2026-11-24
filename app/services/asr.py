"""
Bilibili RAG 知识库系统

ASR 服务 - 使用 DashScope 录音文件识别
"""
import asyncio
import json
import os
import re
import shutil
import subprocess
import threading
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

    # DashScope Recognition SDK 有全局状态，并发调用会导致结果混淆
    # 用类级别锁确保同一时刻只有一个 Recognition.call() 在执行
    _recognition_lock = threading.Lock()

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
        self._unavailable_models: dict[str, str] = {}
        self._availability_lock = threading.Lock()

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

    @staticmethod
    def _model_sample_rate(model: str) -> int:
        return 8000 if "-8k-" in model else 16000

    def _transcription_parameters(self, model: str) -> dict:
        if model != "paraformer-v2":
            return {}
        return {"language_hints": ["zh", "en"]}

    def _skip_unavailable_model(self, model: str) -> bool:
        with self._availability_lock:
            code = self._unavailable_models.get(model)
        if code:
            logger.warning("[ASR] 跳过本次运行已不可用的模型 {}: code={}", model, code)
            return True
        return False

    def _record_model_error(self, model: str, code: Any) -> None:
        if not isinstance(code, str):
            return
        unavailable = code.startswith("AllocationQuota.") or code in {
            "InvalidApiKey", "InvalidApiKeyError", "AccessDenied", "Unauthorized",
            "ModelNotFound", "ModelNotExist", "InvalidModel",
        }
        if not unavailable:
            return
        with self._availability_lock:
            if model in self._unavailable_models:
                return
            self._unavailable_models[model] = code
        logger.warning(
            "[ASR] 模型 {} 已标记为本次运行不可用: code={}；后续条目跳过，下次启动重新尝试",
            model, code,
        )

    def _log_asr_errors(self, model: str, stage: str, response: Any, task_id: Optional[str] = None) -> None:
        """Log error fields only, including nested file results, without signed URLs."""
        def safe(value: Any) -> str:
            text = str(value if value is not None else "-")
            if self.api_key:
                text = text.replace(self.api_key, "[REDACTED]")
            return re.sub(r"(?:https?|oss|wss?)://\S+", "[URL]", text)

        def visit(node: Any, path: str, request_id: Any = None, force: bool = False) -> None:
            request_id = self._get_output_value(node, "request_id") or request_id
            code = self._get_output_value(node, "code") or self._get_output_value(node, "error_code")
            self._record_model_error(model, code)
            messages = [
                self._get_output_value(node, key)
                for key in ("status_message", "error_message", "message")
            ]
            message = "; ".join(str(value) for value in messages if value)
            status = (
                self._get_output_value(node, "task_status")
                or self._get_output_value(node, "subtask_status")
            )
            if force or code or message or status in ("FAILED", "CANCELED", "CANCELLED"):
                logger.warning(
                    "[ASR] 模型 {} {} 失败详情: path={}, task_id={}, status={}, "
                    "status_code={}, code={}, message={}, request_id={}",
                    model, stage, path, task_id or "-", safe(status),
                    safe(self._get_output_value(node, "status_code")), safe(code),
                    safe(message or None), safe(request_id),
                )
            output = self._get_output_value(node, "output")
            if isinstance(output, dict):
                visit(output, f"{path}.output", request_id)
            results = self._get_output_value(node, "results", [])
            if isinstance(results, list):
                for index, item in enumerate(results):
                    if isinstance(item, dict):
                        visit(item, f"{path}.results[{index}]", request_id)

        visit(response, "response", force=True)

    def _transcode_audio_to_pcm(self, file_path: str, sample_rate: int = 16000) -> Optional[str]:
        """按模型采样率转码为单声道 s16le PCM，适配 Recognition"""
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            logger.info("未检测到 ffmpeg，无法转码为 PCM")
            return None
        base, _ext = os.path.splitext(file_path)
        pcm_path = base + (".8k.pcm" if sample_rate == 8000 else ".pcm")
        if pcm_path == file_path:
            pcm_path = base + ".16k.pcm"
        cmd = [
            ffmpeg,
            "-y",
            "-i", file_path,
            "-f", "s16le",
            "-acodec", "pcm_s16le",
            "-ac", "1",
            "-ar", str(sample_rate),
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

    def _transcode_audio_to_wav(self, file_path: str, sample_rate: int = 16000) -> Optional[str]:
        """按模型采样率转码为单声道 WAV"""
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            logger.info("未检测到 ffmpeg，无法转码为 WAV")
            return None
        base, _ext = os.path.splitext(file_path)
        wav_path = base + (".8k.wav" if sample_rate == 8000 else ".wav")
        if wav_path == file_path:
            wav_path = base + ".16k.wav"
        cmd = [
            ffmpeg,
            "-y",
            "-i", file_path,
            "-ac", "1",
            "-ar", str(sample_rate),
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

    def _prepare_recognition_input(self, file_path: str, sample_rate: int = 16000) -> Optional[str]:
        """按输入格式准备 Recognition 文件"""
        fmt = (self.input_format or "pcm").lower()
        if fmt == "wav":
            return self._transcode_audio_to_wav(file_path, sample_rate=sample_rate)
        return self._transcode_audio_to_pcm(file_path, sample_rate=sample_rate)

    def _recognize_local_file(self, file_path: str, title: Optional[str] = None) -> Optional[str]:
        """使用 Recognition 直传本地音频，支持多模型自动 fallback"""
        self._configure()
        if not os.path.exists(file_path):
            logger.warning(f"ASR 本地文件不存在: {file_path}")
            return None

        for model in self.local_models:
            if self._skip_unavailable_model(model):
                continue
            sample_rate = self._model_sample_rate(model)
            input_path = self._prepare_recognition_input(file_path, sample_rate=sample_rate)
            if not input_path:
                logger.warning(f"ASR Recognition 模型 {model} 音频准备失败，尝试下一个...")
                continue
            logger.info(
                f"ASR Recognition 使用模型: {model}, format={self.input_format or 'pcm'}, sample_rate={sample_rate}"
            )
            try:
                recognizer = Recognition(
                    model=model,
                    callback=None,
                    format=(self.input_format or "pcm"),
                    sample_rate=sample_rate,
                )
                with ASRService._recognition_lock:
                    if self._skip_unavailable_model(model):
                        continue
                    result = recognizer.call(input_path)
                    status_code = getattr(result, "status_code", None)
                    if isinstance(status_code, int) and status_code != HTTPStatus.OK:
                        self._log_asr_errors(model, "Recognition", result)
                logger.info(
                    "ASR Recognition 结果: model={}, status_code={}",
                    model, status_code,
                )
                if isinstance(status_code, int) and status_code != HTTPStatus.OK:
                    if getattr(result, "code", None) == "AllocationQuota.FreeTierOnly":
                        logger.warning(
                            "[ASR] 模型 {} 被服务端免费额度策略拒绝；请核对 API Key 所属账号、"
                            "地域和工作空间的额度，不会自动开启付费调用",
                            model,
                        )
                    continue
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

    @staticmethod
    def _rest_response_data(response: httpx.Response) -> dict:
        try:
            data = response.json()
        except ValueError:
            data = {"code": "InvalidResponse", "message": "ASR 响应不是有效 JSON"}
        if not isinstance(data, dict):
            data = {"code": "InvalidResponse", "message": "ASR 响应不是 JSON 对象"}
        return {**data, "status_code": response.status_code}

    def _submit_transcription_task_restful(self, audio_url: str, model: str) -> Optional[str]:
        url = self._build_api_url("services", "audio", "asr", "transcription")
        headers = {
            **default_headers(self.api_key),
            "Content-Type": "application/json",
            "X-DashScope-Async": "enable",
        }
        if audio_url.startswith("oss://"):
            headers["X-DashScope-OssResourceResolve"] = "enable"
        parameters = self._transcription_parameters(model)
        payload = {"model": model, "input": {"file_urls": [audio_url]}}
        if parameters:
            payload["parameters"] = parameters

        try:
            resp = httpx.post(url, json=payload, headers=headers, timeout=30.0)
        except Exception as e:
            logger.warning(f"ASR RESTful 提交失败: {e}")
            return None

        data = self._rest_response_data(resp)
        if resp.status_code != HTTPStatus.OK or data.get("code"):
            self._log_asr_errors(model, "RESTful 提交", data)
            return None

        task_id = data.get("task_id")
        if not task_id:
            output = data.get("output") if isinstance(data, dict) else None
            if isinstance(output, dict):
                task_id = output.get("task_id")
        if not task_id:
            self._log_asr_errors(model, "RESTful 提交未返回 task_id", data)
        return task_id

    def _fetch_transcription_task_restful(self, task_id: str) -> Optional[dict]:
        url = self._build_api_url("tasks", task_id)
        headers = default_headers(self.api_key)
        try:
            resp = httpx.get(url, headers=headers, timeout=30.0)
        except Exception as e:
            logger.warning(f"ASR RESTful 查询失败: {e}")
            return None

        data = self._rest_response_data(resp)
        if resp.status_code != HTTPStatus.OK or data.get("code"):
            return data
        if isinstance(data, dict) and isinstance(data.get("output"), dict):
            return {
                **data["output"], "status_code": resp.status_code,
                "request_id": data.get("request_id") or data["output"].get("request_id"),
            }
        return data if isinstance(data, dict) else None

    def _transcribe_sync_restful(self, audio_url: str, model: str, title: Optional[str] = None) -> Optional[str]:
        self._configure()
        if self._skip_unavailable_model(model):
            return None
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
            status_code = output.get("status_code")
            if (
                (status_code is not None and status_code != HTTPStatus.OK)
                or (not status and (output.get("code") or output.get("error_code")))
            ):
                self._log_asr_errors(model, "RESTful 查询", output, task_id=task_id)
                return None
            if status in ("SUCCEEDED", "FAILED", "CANCELED", "CANCELLED"):
                break
            time.sleep(1.5)

        task_status = self._get_output_value(output, "task_status")
        results = self._get_output_value(output, "results", []) or []
        logger.info(
            "[ASR] 模型 {} 任务状态(RESTful): task_id={}, task_status={}, results={}",
            model, task_id, task_status, len(results),
        )

        if task_status in ("FAILED", "CANCELED", "CANCELLED"):
            self._log_asr_errors(model, "RESTful", output, task_id=task_id)
            return None

        if any(item.get("subtask_status") in ("FAILED", "CANCELED", "CANCELLED") for item in results):
            self._log_asr_errors(model, "RESTful 子任务", output, task_id=task_id)
        for item in results:
            sub_status = item.get("subtask_status")
            transcription_url = item.get("transcription_url")
            if sub_status:
                logger.info(
                    "[ASR] 模型 {} 子任务(RESTful): subtask_status={}, has_url={}",
                    model, sub_status, bool(transcription_url),
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
        if self._skip_unavailable_model(model):
            return None
        if audio_url.startswith("oss://"):
            return self._transcribe_sync_restful(audio_url, model, title=title)

        kwargs = self._transcription_parameters(model)

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
            self._log_asr_errors(model, "SDK 提交", resp)
            logger.warning(f"[ASR] 模型 {model} 未返回 task_id")
            return None
        logger.info(f"[ASR] 模型 {model} 任务已提交: task_id={task_id}")

        start = time.time()
        while True:
            status_code = getattr(resp, "status_code", None)
            if status_code is not None and status_code != HTTPStatus.OK:
                self._log_asr_errors(model, "SDK 请求", resp, task_id=task_id)
                return None
            status = self._get_output_value(output, "task_status")
            if status in ("SUCCEEDED", "FAILED", "CANCELED", "CANCELLED"):
                break
            if time.time() - start > self.timeout:
                logger.warning(f"[ASR] 模型 {model} 任务超时")
                return None
            time.sleep(1.5)
            resp = Transcription.fetch(task=task_id)
            output = getattr(resp, "output", None)

        status_code = getattr(resp, "status_code", None)
        task_status = self._get_output_value(output, "task_status")
        logger.info(
            "[ASR] 模型 {} 任务状态: task_id={}, task_status={}, status_code={}",
            model, task_id, task_status, status_code,
        )

        if task_status in ("FAILED", "CANCELED", "CANCELLED"):
            self._log_asr_errors(model, "SDK", resp, task_id=task_id)
            return None

        if status_code != HTTPStatus.OK:
            self._log_asr_errors(model, "SDK 请求", resp, task_id=task_id)
            return None

        results = self._get_output_value(output, "results", []) or []
        if any(item.get("subtask_status") in ("FAILED", "CANCELED", "CANCELLED") for item in results):
            self._log_asr_errors(model, "SDK 子任务", resp, task_id=task_id)
        for item in results:
            sub_status = item.get("subtask_status")
            transcription_url = item.get("transcription_url")
            if sub_status:
                logger.info(
                    "[ASR] 模型 {} 子任务: subtask_status={}, has_url={}",
                    model, sub_status, bool(transcription_url),
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
        """本地文件优先上传后录音识别，失败再回退 Recognition"""
        return await asyncio.to_thread(self._transcribe_local_file_sync, file_path, title)

    def _transcribe_local_file_sync(self, file_path: str, title: Optional[str] = None) -> Optional[str]:
        self._configure()
        if not os.path.exists(file_path):
            logger.warning(f"ASR 本地文件不存在: {file_path}")
            return None

        for model in self.models:
            if self._skip_unavailable_model(model):
                continue
            input_path = self._transcode_audio_to_wav(
                file_path, sample_rate=self._model_sample_rate(model)
            )
            if not input_path:
                logger.warning(f"[ASR] 录音模型 {model} 音频准备失败，尝试下一个...")
                continue
            audio_url = self._upload_temp_file(input_path, model=model)
            if not audio_url:
                logger.warning(f"[ASR] 录音模型 {model} 上传失败，尝试下一个...")
                continue
            text = self._try_transcribe_with_model(audio_url, model, title=title)
            if text:
                return text
            logger.warning(f"[ASR] 录音模型 {model} 转写失败，尝试下一个...")

        logger.warning("[ASR] 本地文件录音识别失败，回退到 Recognition")
        return self._recognize_local_file(file_path, title=title)

    def _transcribe_sync_with_model(self, audio_url: str, model: str, title: Optional[str] = None) -> Optional[str]:
        """使用指定单个模型转写（用于本地文件上传后的直接调用）"""
        return self._try_transcribe_with_model(audio_url, model, title=title)
