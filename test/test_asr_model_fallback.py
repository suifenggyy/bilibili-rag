import tempfile
import unittest
from http import HTTPStatus
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from app.services.asr import ASRService


class ASRModelFallbackTests(unittest.TestCase):
    def setUp(self):
        with patch("app.services.asr.ASRTempFileManager"):
            self.service = ASRService(api_key="test-key")

    def test_recognition_fallback_prepares_matching_sample_rates(self):
        self.service.local_models = [
            "paraformer-realtime-v2",
            "paraformer-realtime-8k-v2",
        ]
        empty_result = Mock()
        empty_result.get_sentence.return_value = []
        success_result = Mock()
        success_result.get_sentence.return_value = [{"text": "transcript"}]
        with (
            patch("app.services.asr.os.path.exists", return_value=True),
            patch.object(
                self.service, "_prepare_recognition_input",
                side_effect=["audio.pcm", "audio.8k.pcm"],
            ) as prepare,
            patch("app.services.asr.Recognition") as recognition,
        ):
            recognition.return_value.call.side_effect = [empty_result, success_result]
            text = self.service._recognize_local_file("audio.mp3")

        self.assertEqual(text, "transcript")
        self.assertEqual(prepare.call_args_list, [
            call("audio.mp3", sample_rate=16000),
            call("audio.mp3", sample_rate=8000),
        ])
        self.assertEqual(
            [entry.kwargs["sample_rate"] for entry in recognition.call_args_list],
            [16000, 8000],
        )
        self.assertEqual(
            recognition.return_value.call.call_args_list,
            [call("audio.pcm"), call("audio.8k.pcm")],
        )

    def test_failed_preparation_continues_to_next_model(self):
        self.service.local_models = [
            "paraformer-realtime-v2",
            "paraformer-realtime-8k-v2",
        ]
        with (
            patch("app.services.asr.os.path.exists", return_value=True),
            patch.object(self.service, "_prepare_recognition_input",
                         side_effect=[None, "audio.8k.pcm"]),
            patch("app.services.asr.Recognition") as recognition,
        ):
            recognition.return_value.call.return_value.get_sentence.return_value = [
                {"text": "transcript"}
            ]
            self.assertEqual(
                self.service._recognize_local_file("audio.mp3"), "transcript"
            )
        recognition.assert_called_once_with(
            model="paraformer-realtime-8k-v2",
            callback=None, format="pcm", sample_rate=8000,
        )

    def test_transcoding_uses_distinct_8k_paths_and_preserves_16k_default(self):
        for fmt in ("pcm", "wav"):
            with self.subTest(fmt=fmt), tempfile.TemporaryDirectory() as directory:
                self.service.input_format = fmt
                source = str(Path(directory) / "audio.mp3")
                with (
                    patch("app.services.asr.shutil.which", return_value="/bin/ffmpeg"),
                    patch("app.services.asr.subprocess.run",
                          return_value=SimpleNamespace(returncode=0)) as run,
                ):
                    default_path = self.service._prepare_recognition_input(source)
                    narrowband_path = self.service._prepare_recognition_input(
                        source, sample_rate=8000
                    )
                self.assertEqual(default_path, str(Path(directory) / f"audio.{fmt}"))
                self.assertEqual(
                    narrowband_path, str(Path(directory) / f"audio.8k.{fmt}")
                )
                commands = [entry.args[0] for entry in run.call_args_list]
                self.assertEqual(
                    [cmd[cmd.index("-ar") + 1] for cmd in commands],
                    ["16000", "8000"],
                )

    def test_url_fallback_uses_recorded_models_in_order(self):
        self.service.models = ["paraformer-v2", "paraformer-8k-v2"]
        with patch.object(self.service, "_try_transcribe_with_model",
                          side_effect=[None, "transcript"]) as transcribe:
            self.assertEqual(
                self.service._transcribe_sync("https://example.com/audio.mp3"),
                "transcript",
            )
        self.assertEqual(transcribe.call_args_list, [
            call("https://example.com/audio.mp3", "paraformer-v2", None),
            call("https://example.com/audio.mp3", "paraformer-8k-v2", None),
        ])

    def test_transcoding_never_overwrites_source_audio(self):
        for fmt in ("pcm", "wav"):
            with self.subTest(fmt=fmt):
                self.service.input_format = fmt
                source = f"/tmp/audio.{fmt}"
                with (
                    patch("app.services.asr.shutil.which", return_value="/bin/ffmpeg"),
                    patch("app.services.asr.subprocess.run",
                          return_value=SimpleNamespace(returncode=0)) as run,
                ):
                    output = self.service._prepare_recognition_input(source)
                self.assertNotEqual(output, source)
                self.assertNotEqual(run.call_args.args[0][-1], source)

    def test_language_hints_are_only_sent_to_supported_recorded_model(self):
        for model, languages in (
            ("paraformer-v2", ["zh", "en"]),
            ("paraformer-8k-v2", None),
        ):
            with self.subTest(model=model):
                response = SimpleNamespace(
                    output={"task_id": "task", "task_status": "FAILED"}
                )
                with patch("app.services.asr.Transcription.async_call",
                           return_value=response) as submit:
                    self.service._try_transcribe_with_model(
                        "https://example.com/audio.mp3", model
                    )
                if languages is None:
                    self.assertNotIn("language_hints", submit.call_args.kwargs)
                else:
                    self.assertEqual(submit.call_args.kwargs["language_hints"], languages)

                rest_response = Mock(status_code=HTTPStatus.OK)
                rest_response.json.return_value = {"output": {"task_id": "task"}}
                with patch("app.services.asr.httpx.post",
                           return_value=rest_response) as post:
                    self.service._submit_transcription_task_restful(
                        "oss://audio.mp3", model
                    )
                parameters = post.call_args.kwargs["json"].get("parameters", {})
                if languages is None:
                    self.assertNotIn("language_hints", parameters)
                else:
                    self.assertEqual(parameters["language_hints"], languages)

    def test_temporary_oss_requests_enable_resource_resolution(self):
        response = Mock(status_code=HTTPStatus.OK)
        response.json.return_value = {"output": {"task_id": "task"}}
        with patch("app.services.asr.httpx.post", return_value=response) as post:
            self.service._submit_transcription_task_restful("oss://private/audio.wav", "paraformer-v2")
        self.assertEqual(
            post.call_args.kwargs["headers"].get("X-DashScope-OssResourceResolve"), "enable",
        )

    def test_public_audio_requests_do_not_enable_oss_resolution(self):
        response = Mock(status_code=HTTPStatus.OK)
        response.json.return_value = {"output": {"task_id": "task"}}
        with patch("app.services.asr.httpx.post", return_value=response) as post:
            self.service._submit_transcription_task_restful("https://example.com/audio.wav", "paraformer-v2")
        self.assertNotIn("X-DashScope-OssResourceResolve", post.call_args.kwargs["headers"])

    def test_submission_error_reports_fields_without_dumping_response_body(self):
        response = Mock(status_code=HTTPStatus.BAD_REQUEST)
        response.json.return_value = {
            "code": "InvalidParameter", "message": "invalid audio", "request_id": "submit-request",
            "input": {"api_key": "test-key", "file_urls": ["oss://private/audio.wav"]},
        }
        response.text = "test-key oss://private/audio.wav"
        with (
            patch("app.services.asr.httpx.post", return_value=response),
            patch("app.services.asr.logger.warning") as warning,
        ):
            self.assertIsNone(self.service._submit_transcription_task_restful("oss://audio", "paraformer-v2"))
        messages = "\n".join(entry.args[0].format(*entry.args[1:]) for entry in warning.call_args_list)
        for expected in ("400", "InvalidParameter", "invalid audio", "submit-request"):
            self.assertIn(expected, messages)
        self.assertNotIn("test-key", messages)
        self.assertNotIn("oss://private", messages)

    def test_polling_api_error_stops_immediately_with_request_id(self):
        response = Mock(status_code=HTTPStatus.FORBIDDEN)
        response.json.return_value = {
            "code": "AccessDenied", "message": "workspace denied", "request_id": "poll-request",
        }
        response.text = "access denied"
        self.service.timeout = 1
        with (
            patch.object(self.service, "_submit_transcription_task_restful", return_value="task"),
            patch("app.services.asr.httpx.get", return_value=response),
            patch("app.services.asr.time.time", side_effect=[0, 0, 2]),
            patch("app.services.asr.time.sleep") as sleep,
            patch("app.services.asr.logger.warning") as warning,
        ):
            self.assertIsNone(self.service._transcribe_sync_restful("oss://audio", "paraformer-v2"))
        sleep.assert_not_called()
        messages = "\n".join(entry.args[0].format(*entry.args[1:]) for entry in warning.call_args_list)
        self.assertIn("AccessDenied", messages)
        self.assertIn("poll-request", messages)

    def test_sdk_submission_error_reports_request_id(self):
        response = SimpleNamespace(
            status_code=HTTPStatus.BAD_REQUEST, code="InvalidParameter",
            message="invalid model", request_id="sdk-submit-request", output={},
        )
        with (
            patch("app.services.asr.Transcription.async_call", return_value=response),
            patch("app.services.asr.logger.warning") as warning,
        ):
            self.assertIsNone(self.service._try_transcribe_with_model("https://example.com/audio.wav", "paraformer-v2"))
        messages = "\n".join(entry.args[0].format(*entry.args[1:]) for entry in warning.call_args_list)
        self.assertIn("InvalidParameter", messages)
        self.assertIn("sdk-submit-request", messages)

    def test_error_messages_redact_urls_and_api_key_at_all_log_levels(self):
        output = {
            "task_status": "FAILED",
            "status_message": "cannot read oss://private/audio.wav?token=secret",
            "results": [{"subtask_status": "FAILED", "error_code": "DownloadError",
                         "error_message": "test-key https://example.com/audio?signature=secret"}],
        }
        with (
            patch.object(self.service, "_submit_transcription_task_restful", return_value="task"),
            patch.object(self.service, "_fetch_transcription_task_restful", return_value=output),
            patch("app.services.asr.logger") as log,
        ):
            self.assertIsNone(self.service._transcribe_sync_restful("oss://audio", "paraformer-v2"))
        messages = "\n".join(
            entry.args[0].format(*entry.args[1:])
            for entry in log.info.call_args_list + log.warning.call_args_list
        )
        self.assertIn("DownloadError", messages)
        for secret in ("test-key", "oss://private", "signature=secret", "token=secret"):
            self.assertNotIn(secret, messages)

    def test_rest_failure_logs_nested_codes_messages_and_request_ids(self):
        response = {
            "request_id": "top-request",
            "output": {
                "task_status": "FAILED", "code": "SERVER_ERROR",
                "results": [{
                    "subtask_status": "FAILED", "code": "SERVER_ERROR",
                    "message": "/ by zero", "request_id": "sub-request",
                    "file_url": "oss://private/audio.wav",
                    "results": [{"code": "DECODE_ERROR", "message": "bad audio", "request_id": "deep-request"}],
                }],
            },
        }
        fetch_response = Mock(status_code=HTTPStatus.OK)
        fetch_response.json.return_value = response
        with (
            patch.object(self.service, "_submit_transcription_task_restful", return_value="task"),
            patch("app.services.asr.httpx.get", return_value=fetch_response),
            patch("app.services.asr.logger.warning") as warning,
        ):
            self.assertIsNone(self.service._transcribe_sync_restful("oss://audio", "paraformer-v2"))
        messages = "\n".join(entry.args[0].format(*entry.args[1:]) for entry in warning.call_args_list)
        for expected in ("SERVER_ERROR", "/ by zero", "top-request", "sub-request", "deep-request", "DECODE_ERROR"):
            self.assertIn(expected, messages)
        self.assertNotIn("oss://private", messages)

    def test_sdk_failure_logs_nested_error_details(self):
        response = SimpleNamespace(
            status_code=HTTPStatus.OK, request_id="sdk-request", code=None, message=None,
            output={"task_id": "task", "task_status": "FAILED", "results": [
                {"subtask_status": "FAILED", "code": "SERVER_ERROR", "message": "/ by zero"}
            ]},
        )
        with (
            patch("app.services.asr.Transcription.async_call", return_value=response),
            patch("app.services.asr.logger.warning") as warning,
        ):
            self.assertIsNone(self.service._try_transcribe_with_model("https://example.com/audio.wav", "paraformer-v2"))
        messages = "\n".join(entry.args[0].format(*entry.args[1:]) for entry in warning.call_args_list)
        self.assertIn("SERVER_ERROR", messages)
        self.assertIn("/ by zero", messages)
        self.assertIn("sdk-request", messages)

    def test_failed_and_canceled_subtasks_inherit_response_request_id(self):
        for transport in ("rest", "sdk"):
            for status in ("FAILED", "CANCELED", "CANCELLED"):
                with self.subTest(transport=transport, status=status):
                    output = {
                        "task_id": "task", "task_status": "SUCCEEDED",
                        "results": [{"subtask_status": status, "error_code": "AudioError",
                                     "error_message": "subtask rejected"}],
                    }
                    with patch("app.services.asr.logger.warning") as warning:
                        if transport == "rest":
                            with (
                                patch.object(self.service, "_submit_transcription_task_restful", return_value="task"),
                                patch.object(self.service, "_fetch_transcription_task_restful",
                                             return_value={**output, "request_id": "enclosing-request"}),
                            ):
                                self.assertIsNone(self.service._transcribe_sync_restful("oss://audio", "paraformer-v2"))
                        else:
                            response = SimpleNamespace(status_code=HTTPStatus.OK, output=output,
                                                       request_id="enclosing-request")
                            with patch("app.services.asr.Transcription.async_call", return_value=response):
                                self.assertIsNone(self.service._try_transcribe_with_model(
                                    "https://example.com/audio", "paraformer-v2",
                                ))
                    messages = "\n".join(entry.args[0].format(*entry.args[1:]) for entry in warning.call_args_list)
                    for expected in ("AudioError", "subtask rejected", "enclosing-request"):
                        self.assertIn(expected, messages)

    def test_canceled_rest_task_stops_polling_and_logs_reason(self):
        self.service.timeout = 1
        with (
            patch.object(self.service, "_submit_transcription_task_restful", return_value="task"),
            patch.object(self.service, "_fetch_transcription_task_restful",
                         return_value={"task_status": "CANCELED", "code": "CANCELED", "message": "task canceled"}),
            patch("app.services.asr.time.time", side_effect=[0, 0, 2]),
            patch("app.services.asr.time.sleep") as sleep,
            patch("app.services.asr.logger.warning") as warning,
        ):
            self.assertIsNone(self.service._transcribe_sync_restful("oss://audio", "paraformer-v2"))
        sleep.assert_not_called()
        messages = "\n".join(entry.args[0].format(*entry.args[1:]) for entry in warning.call_args_list)
        self.assertIn("task canceled", messages)

    def test_recognition_quota_error_is_not_reported_as_empty_transcript(self):
        self.service.local_models = ["paraformer-realtime-v2"]
        result = Mock(
            status_code=44, code="AllocationQuota.FreeTierOnly", message="free tier rejected",
            request_id="quota-request",
        )
        with (
            patch("app.services.asr.os.path.exists", return_value=True),
            patch.object(self.service, "_prepare_recognition_input", return_value="audio.pcm"),
            patch("app.services.asr.Recognition") as recognition,
            patch("app.services.asr.logger.warning") as warning,
        ):
            recognition.return_value.call.return_value = result
            self.assertIsNone(self.service._recognize_local_file("audio.wav"))
        result.get_sentence.assert_not_called()
        messages = "\n".join(entry.args[0].format(*entry.args[1:]) for entry in warning.call_args_list)
        self.assertIn("AllocationQuota.FreeTierOnly", messages)
        self.assertIn("quota-request", messages)
        self.assertIn("工作空间", messages)

    def test_quota_rejected_model_is_skipped_for_subsequent_audio(self):
        self.service.models = ["paraformer-v2", "paraformer-8k-v2"]
        quota = SimpleNamespace(
            status_code=HTTPStatus.OK, request_id="quota-request",
            output={"task_id": "task", "task_status": "FAILED", "results": [
                {"subtask_status": "FAILED", "code": "AllocationQuota.FreeTierOnly", "message": "quota rejected"}
            ]},
        )
        success = SimpleNamespace(
            status_code=HTTPStatus.OK,
            output={"task_id": "success", "task_status": "SUCCEEDED", "results": [
                {"subtask_status": "SUCCEEDED", "transcription_url": "https://example.com/result"}
            ]},
        )
        with (
            patch("app.services.asr.Transcription.async_call", side_effect=[quota, success, success]) as submit,
            patch.object(self.service, "_download_transcription", return_value="transcript"),
        ):
            self.assertEqual(self.service._transcribe_sync("https://example.com/first.wav"), "transcript")
            self.assertEqual(self.service._transcribe_sync("https://example.com/second.wav"), "transcript")
        self.assertEqual(
            [entry.kwargs["model"] for entry in submit.call_args_list],
            ["paraformer-v2", "paraformer-8k-v2", "paraformer-8k-v2"],
        )

    def test_audio_specific_server_error_does_not_disable_model(self):
        self.service.models = ["paraformer-v2"]
        failure = SimpleNamespace(
            status_code=HTTPStatus.OK,
            output={"task_id": "task", "task_status": "FAILED", "results": [
                {"subtask_status": "FAILED", "code": "SERVER_ERROR", "message": "/ by zero"}
            ]},
        )
        with patch("app.services.asr.Transcription.async_call", return_value=failure) as submit:
            self.service._transcribe_sync("https://example.com/first.wav")
            self.service._transcribe_sync("https://example.com/second.wav")
        self.assertEqual(submit.call_count, 2)

    def test_recognition_quota_rejection_is_skipped_on_next_item(self):
        self.service.local_models = ["paraformer-realtime-v2"]
        result = SimpleNamespace(
            status_code=44, code="AllocationQuota.FreeTierOnly",
            message="quota rejected", request_id="quota-request",
        )
        with (
            patch("app.services.asr.os.path.exists", return_value=True),
            patch.object(self.service, "_prepare_recognition_input", return_value="audio.pcm") as prepare,
            patch("app.services.asr.Recognition") as recognition,
        ):
            recognition.return_value.call.return_value = result
            self.assertIsNone(self.service._recognize_local_file("first.wav"))
            self.assertIsNone(self.service._recognize_local_file("second.wav"))
        recognition.assert_called_once()
        prepare.assert_called_once()

    def test_unavailable_recorded_models_are_skipped_before_upload(self):
        self.service.models = ["paraformer-v2"]
        with (
            patch("app.services.asr.httpx.post") as post,
            patch("app.services.asr.os.path.exists", return_value=True),
            patch.object(self.service, "_transcode_audio_to_wav", return_value="audio.wav") as transcode,
            patch.object(self.service, "_upload_temp_file", return_value="oss://audio") as upload,
            patch.object(self.service, "_recognize_local_file", return_value=None),
        ):
            response = Mock(status_code=HTTPStatus.FORBIDDEN)
            response.json.return_value = {"code": "AccessDenied", "message": "model denied"}
            post.return_value = response
            self.service._transcribe_local_file_sync("first.wav")
            self.service._transcribe_local_file_sync("second.wav")
        self.assertEqual(post.call_count, 1)
        self.assertEqual(upload.call_count, 1)
        self.assertEqual(transcode.call_count, 1)

    def test_new_service_does_not_inherit_model_rejections(self):
        self.service.models = ["paraformer-v2"]
        failure = SimpleNamespace(
            status_code=HTTPStatus.FORBIDDEN, code="AccessDenied", message="denied", output={},
        )
        with (
            patch("app.services.asr.Transcription.async_call", return_value=failure) as submit,
            patch("app.services.asr.ASRTempFileManager"),
        ):
            self.service._transcribe_sync("https://example.com/first.wav")
            restarted = ASRService(api_key="test-key", model="paraformer-v2")
            restarted._transcribe_sync("https://example.com/second.wav")
        self.assertEqual(submit.call_count, 2)


class ASRLocalFileFallbackTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        with patch("app.services.asr.ASRTempFileManager"):
            self.service = ASRService(api_key="test-key")
        self.service.models = ["paraformer-v2", "paraformer-8k-v2"]

    async def test_local_file_uploads_for_recorded_model_before_recognition(self):
        with (
            patch("app.services.asr.os.path.exists", return_value=True),
            patch.object(self.service, "_transcode_audio_to_wav",
                         return_value="audio.wav") as transcode,
            patch.object(self.service, "_upload_temp_file",
                         return_value="oss://audio.wav") as upload,
            patch.object(self.service, "_try_transcribe_with_model",
                         return_value="transcript") as transcribe,
            patch.object(self.service, "_recognize_local_file") as recognize,
        ):
            self.assertEqual(
                await self.service.transcribe_local_file("audio.mp3", title="video"),
                "transcript",
            )
        transcode.assert_called_once_with("audio.mp3", sample_rate=16000)
        upload.assert_called_once_with("audio.wav", model="paraformer-v2")
        transcribe.assert_called_once_with(
            "oss://audio.wav", "paraformer-v2", title="video"
        )
        recognize.assert_not_called()

    async def test_local_file_recorded_fallback_transcodes_8k(self):
        with (
            patch("app.services.asr.os.path.exists", return_value=True),
            patch.object(self.service, "_transcode_audio_to_wav",
                         side_effect=["audio.wav", "audio.8k.wav"]) as transcode,
            patch.object(self.service, "_upload_temp_file",
                         side_effect=["oss://audio.wav", "oss://audio.8k.wav"]) as upload,
            patch.object(self.service, "_try_transcribe_with_model",
                         side_effect=[None, "transcript"]),
            patch.object(self.service, "_recognize_local_file") as recognize,
        ):
            self.assertEqual(
                await self.service.transcribe_local_file("audio.mp3"), "transcript"
            )
        self.assertEqual(transcode.call_args_list, [
            call("audio.mp3", sample_rate=16000),
            call("audio.mp3", sample_rate=8000),
        ])
        self.assertEqual(upload.call_args_list, [
            call("audio.wav", model="paraformer-v2"),
            call("audio.8k.wav", model="paraformer-8k-v2"),
        ])
        recognize.assert_not_called()

    async def test_local_file_uses_recognition_after_recorded_models_fail(self):
        with (
            patch("app.services.asr.os.path.exists", return_value=True),
            patch.object(self.service, "_transcode_audio_to_wav",
                         return_value="audio.wav"),
            patch.object(self.service, "_upload_temp_file", return_value="oss://audio.wav"),
            patch.object(self.service, "_try_transcribe_with_model",
                         return_value=None) as transcribe,
            patch.object(self.service, "_recognize_local_file",
                         return_value="realtime transcript") as recognize,
        ):
            self.assertEqual(
                await self.service.transcribe_local_file("audio.mp3", title="video"),
                "realtime transcript",
            )
        self.assertEqual(transcribe.call_count, 2)
        recognize.assert_called_once_with("audio.mp3", title="video")

    async def test_upload_failure_skips_transcription_and_falls_back(self):
        with (
            patch("app.services.asr.os.path.exists", return_value=True),
            patch.object(self.service, "_transcode_audio_to_wav", return_value="audio.wav"),
            patch.object(self.service, "_upload_temp_file", return_value=None) as upload,
            patch.object(self.service, "_try_transcribe_with_model") as transcribe,
            patch.object(self.service, "_recognize_local_file",
                         return_value="realtime transcript"),
        ):
            self.assertEqual(
                await self.service.transcribe_local_file("audio.mp3"),
                "realtime transcript",
            )
        self.assertEqual(upload.call_count, 2)
        transcribe.assert_not_called()

    async def test_missing_local_file_does_not_submit_requests(self):
        with (
            patch("app.services.asr.os.path.exists", return_value=False),
            patch.object(self.service, "_upload_temp_file") as upload,
            patch.object(self.service, "_recognize_local_file") as recognize,
        ):
            self.assertIsNone(await self.service.transcribe_local_file("missing.mp3"))
        upload.assert_not_called()
        recognize.assert_not_called()


if __name__ == "__main__":
    unittest.main()
