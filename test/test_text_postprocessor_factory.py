import unittest
from unittest.mock import patch

from app.services.llm.postprocessor_adapter import ChatCompletionPostProcessor


class TextPostProcessorFactoryTests(unittest.TestCase):
    """Test create_text_postprocessor returns ChatCompletionPostProcessor."""

    def test_create_text_postprocessor_returns_chat_completion_postprocessor(self):
        from app.services.text_postprocessor_factory import create_text_postprocessor

        processor = create_text_postprocessor()
        self.assertIsInstance(processor, ChatCompletionPostProcessor)

    def test_create_text_postprocessor_uses_role_prompt(self):
        from app.services.text_postprocessor_factory import create_text_postprocessor
        from app.config import settings

        processor = create_text_postprocessor()
        # Should use the role's prompt from get_llm_config("asr_correction")
        role_config = settings.get_llm_config("asr_correction")
        self.assertEqual(processor._system_prompt, role_config.prompt)

    def test_create_text_postprocessor_uses_custom_prompt(self):
        from app.services.text_postprocessor_factory import create_text_postprocessor

        processor = create_text_postprocessor(prompt_template="Custom prompt")
        self.assertEqual(processor._system_prompt, "Custom prompt")

    def test_create_text_postprocessor_uses_asr_correction_role(self):
        from app.services.text_postprocessor_factory import create_text_postprocessor

        processor = create_text_postprocessor()
        self.assertEqual(processor._role, "asr_correction")

    def test_create_text_postprocessor_with_content_summary_role(self):
        from app.services.text_postprocessor_factory import create_text_postprocessor

        processor = create_text_postprocessor(role="content_summary")
        self.assertEqual(processor._role, "content_summary")

    def test_create_text_postprocessor_content_summary_uses_role_prompt(self):
        from app.services.text_postprocessor_factory import create_text_postprocessor
        from app.config import settings

        processor = create_text_postprocessor(role="content_summary")
        role_config = settings.get_llm_config("content_summary")
        self.assertEqual(processor._system_prompt, role_config.prompt)

    def test_create_text_postprocessor_no_explicit_timeout(self):
        """Timeout should come from the role config, not from a legacy field."""
        from app.services.text_postprocessor_factory import create_text_postprocessor

        processor = create_text_postprocessor()
        # ChatCompletionPostProcessor._timeout is None → role timeout is used
        self.assertIsNone(processor._timeout)


class ContentSummaryServiceConfigTests(unittest.TestCase):
    def test_content_summary_service_uses_content_summary_role(self):
        with patch("app.services.content_summary.create_text_postprocessor") as create_processor:
            from app.services.content_summary import ContentSummaryService

            service = ContentSummaryService()

        create_processor.assert_called_once_with(role="content_summary")
        self.assertIs(service.processor, create_processor.return_value)


if __name__ == "__main__":
    unittest.main()
