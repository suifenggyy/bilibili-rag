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

    def test_sdk_and_rest_use_chinese_only_for_8k_recorded_model(self):
        for model, languages in (
            ("paraformer-v2", ["zh", "en"]),
            ("paraformer-8k-v2", ["zh"]),
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
                self.assertEqual(submit.call_args.kwargs["language_hints"], languages)

                rest_response = Mock(status_code=HTTPStatus.OK)
                rest_response.json.return_value = {"output": {"task_id": "task"}}
                with patch("app.services.asr.httpx.post",
                           return_value=rest_response) as post:
                    self.service._submit_transcription_task_restful(
                        "oss://audio.mp3", model
                    )
                self.assertEqual(
                    post.call_args.kwargs["json"]["parameters"]["language_hints"],
                    languages,
                )


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
