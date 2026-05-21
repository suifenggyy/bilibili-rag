import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch


class ASRBackendFactoryTests(unittest.TestCase):
    def test_create_asr_service_uses_whisper_backend_from_settings(self):
        from app.services.asr_factory import create_asr_service

        fake_settings = SimpleNamespace(
            asr_backend="whisper",
            openai_api_key="",
            ollama_base_url="http://localhost:11434",
            ollama_asr_model="whisper",
            ollama_asr_language="zh",
            whisper_model="turbo",
            whisper_language="zh",
            asr_timeout=600,
        )

        with (
            patch("app.services.asr_factory.settings", fake_settings),
            patch("app.services.asr_factory.OpenAIWhisperASRService") as whisper_cls,
        ):
            service = create_asr_service()

        whisper_cls.assert_called_once_with(
            model="turbo",
            language="zh",
            timeout=600,
        )
        self.assertIs(service, whisper_cls.return_value)

    def test_create_asr_service_uses_ollama_override(self):
        from app.services.asr_factory import create_asr_service

        fake_settings = SimpleNamespace(
            asr_backend="whisper",
            openai_api_key="",
            ollama_base_url="http://localhost:11434",
            ollama_asr_model="whisper",
            ollama_asr_language="zh",
            whisper_model="turbo",
            whisper_language="zh",
            asr_timeout=600,
        )

        with (
            patch("app.services.asr_factory.settings", fake_settings),
            patch("app.services.asr_factory.OllamaASRService") as ollama_cls,
        ):
            service = create_asr_service(
                backend="ollama",
                ollama_base_url="http://ollama.internal:11434",
                ollama_model="whisper:large",
                ollama_language="en",
            )

        ollama_cls.assert_called_once_with(
            base_url="http://ollama.internal:11434",
            model="whisper:large",
            language="en",
            timeout=600,
        )
        self.assertIs(service, ollama_cls.return_value)


class OpenAIWhisperASRServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_transcribe_local_file_calls_whisper_model_and_returns_text(self):
        from app.services.asr_whisper import OpenAIWhisperASRService

        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
            tmp.write(b"fake audio bytes")
            audio_path = tmp.name

        fake_model = SimpleNamespace(transcribe=lambda path, language=None: {"text": "  hello whisper  "})

        try:
            with patch("app.services.asr_whisper.whisper.load_model", return_value=fake_model) as load_model:
                service = OpenAIWhisperASRService(model="turbo", language="zh", timeout=321)
                text = await service.transcribe_local_file(audio_path)

            load_model.assert_called_once_with("turbo")
            self.assertEqual(text, "hello whisper")
        finally:
            os.remove(audio_path)


if __name__ == "__main__":
    unittest.main()
