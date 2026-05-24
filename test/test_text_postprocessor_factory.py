import unittest
from types import SimpleNamespace
from unittest.mock import patch


class TextPostProcessorFactoryTests(unittest.TestCase):
    def test_create_text_postprocessor_uses_proxy_backend_from_settings(self):
        from app.services.text_postprocessor_factory import create_text_postprocessor

        fake_settings = SimpleNamespace(
            text_model_backend="proxy",
            text_model_base_url="http://localhost:4141",
            text_model_name="qwen-local",
            text_model_correction_prompt="请纠错",
            text_model_timeout=180,
            local_openai_base_url="http://localhost:4141",
            local_openai_model="",
        )

        with (
            patch("app.services.text_postprocessor_factory.settings", fake_settings),
            patch("app.services.text_postprocessor_factory.ProxyTextPostProcessor") as proxy_cls,
        ):
            processor = create_text_postprocessor()

        proxy_cls.assert_called_once_with(
            base_url="http://localhost:4141",
            model="qwen-local",
            prompt_template="请纠错",
            timeout=180,
        )
        self.assertIs(processor, proxy_cls.return_value)

    def test_create_text_postprocessor_uses_localopenai_backend(self):
        from app.services.text_postprocessor_factory import create_text_postprocessor

        fake_settings = SimpleNamespace(
            text_model_backend="localopenai",
            text_model_base_url="http://localhost:11434",
            text_model_name="fallback-model",
            text_model_correction_prompt="请纠错",
            text_model_timeout=120,
            local_openai_base_url="http://localhost:4141",
            local_openai_model="my-local-model",
        )

        with (
            patch("app.services.text_postprocessor_factory.settings", fake_settings),
            patch("app.services.text_postprocessor_factory.ProxyTextPostProcessor") as proxy_cls,
        ):
            processor = create_text_postprocessor()

        proxy_cls.assert_called_once_with(
            base_url="http://localhost:4141",
            model="my-local-model",
            prompt_template="请纠错",
            timeout=120,
        )
        self.assertIs(processor, proxy_cls.return_value)

    def test_create_text_postprocessor_localopenai_falls_back_to_text_model_name(self):
        from app.services.text_postprocessor_factory import create_text_postprocessor

        fake_settings = SimpleNamespace(
            text_model_backend="localopenai",
            text_model_base_url="http://localhost:11434",
            text_model_name="fallback-model",
            text_model_correction_prompt="请纠错",
            text_model_timeout=120,
            local_openai_base_url="http://localhost:4141",
            local_openai_model="",  # empty → fallback to text_model_name
        )

        with (
            patch("app.services.text_postprocessor_factory.settings", fake_settings),
            patch("app.services.text_postprocessor_factory.ProxyTextPostProcessor") as proxy_cls,
        ):
            create_text_postprocessor()

        _, kwargs = proxy_cls.call_args
        self.assertEqual(kwargs["model"], "fallback-model")
        self.assertEqual(kwargs["base_url"], "http://localhost:4141")

    def test_create_text_postprocessor_uses_ollama_backend_by_default(self):
        from app.services.text_postprocessor_factory import create_text_postprocessor

        fake_settings = SimpleNamespace(
            text_model_backend="ollama",
            text_model_base_url="http://localhost:11434",
            text_model_name="gemma4:e2b",
            text_model_correction_prompt="请纠错",
            text_model_timeout=300,
            local_openai_base_url="http://localhost:4141",
            local_openai_model="",
        )

        with (
            patch("app.services.text_postprocessor_factory.settings", fake_settings),
            patch(
                "app.services.text_postprocessor_factory.OllamaTextPostProcessor"
            ) as ollama_cls,
        ):
            processor = create_text_postprocessor()

        ollama_cls.assert_called_once_with(
            base_url="http://localhost:11434",
            model="gemma4:e2b",
            prompt_template="请纠错",
            timeout=300,
        )
        self.assertIs(processor, ollama_cls.return_value)


class ContentSummaryServiceConfigTests(unittest.TestCase):
    def test_content_summary_service_uses_summary_prompt_from_settings(self):
        fake_settings = SimpleNamespace(
            text_model_summary_prompt="请总结正文",
        )

        with (
            patch("app.services.content_summary.settings", fake_settings),
            patch("app.services.content_summary.create_text_postprocessor") as create_processor,
        ):
            from app.services.content_summary import ContentSummaryService

            service = ContentSummaryService()

        create_processor.assert_called_once_with(prompt_template="请总结正文")
        self.assertIs(service.processor, create_processor.return_value)


if __name__ == "__main__":
    unittest.main()
