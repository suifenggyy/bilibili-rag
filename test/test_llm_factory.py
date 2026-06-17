"""
Unit tests for the unified LLM factory layer.

Tests provider registry, model spec parsing, role configuration resolution,
factory caching, and ChatCompletionPostProcessor compatibility.
"""

import os
import unittest
from unittest.mock import patch, MagicMock, AsyncMock

from app.services.llm.types import LLMMessage, LLMResponse, LLMRoleConfig, LLMProviderConfig
from app.services.llm.factory import get_llm_service, get_langchain_chat, get_openai_client, get_embeddings, reset_factory
from app.services.llm.postprocessor_adapter import ChatCompletionPostProcessor


class TestLLMTypes(unittest.TestCase):
    """Test LLM data types."""

    def test_llm_message_creation(self):
        msg = LLMMessage(role="system", content="Hello")
        self.assertEqual(msg.role, "system")
        self.assertEqual(msg.content, "Hello")

    def test_llm_response_creation(self):
        resp = LLMResponse(content="Hi", model="gpt-4", usage={"prompt_tokens": 5})
        self.assertEqual(resp.content, "Hi")
        self.assertEqual(resp.model, "gpt-4")
        self.assertEqual(resp.usage["prompt_tokens"], 5)

    def test_llm_response_defaults(self):
        resp = LLMResponse(content="test")
        self.assertEqual(resp.model, "")
        self.assertEqual(resp.usage, {})

    def test_llm_role_config_defaults(self):
        config = LLMRoleConfig()
        self.assertEqual(config.provider, "")
        self.assertEqual(config.temperature, 0.5)
        self.assertEqual(config.timeout, 300)
        self.assertEqual(config.max_tokens, 4096)
        self.assertEqual(config.prompt, "")

    def test_llm_provider_config(self):
        config = LLMProviderConfig(name="deepseek", base_url="https://api.deepseek.com/v1", api_key="sk-xxx")
        self.assertEqual(config.name, "deepseek")
        self.assertEqual(config.base_url, "https://api.deepseek.com/v1")
        self.assertEqual(config.api_key, "sk-xxx")


class TestModelSpecParsing(unittest.TestCase):
    """Test _parse_model_spec and _expand_model_spec."""

    def test_parse_provider_model(self):
        from app.config import Settings
        provider, model = Settings._parse_model_spec("dashscope:qwen3-max")
        self.assertEqual(provider, "dashscope")
        self.assertEqual(model, "qwen3-max")

    def test_parse_model_with_colon_in_name(self):
        from app.config import Settings
        provider, model = Settings._parse_model_spec("ollama:qwen3:35b")
        self.assertEqual(provider, "ollama")
        self.assertEqual(model, "qwen3:35b")

    def test_parse_no_colon_returns_provider_only(self):
        from app.config import Settings
        provider, model = Settings._parse_model_spec("dashscope")
        self.assertEqual(provider, "dashscope")
        self.assertEqual(model, "")

    def test_expand_model_spec(self):
        from app.config import Settings
        s = Settings()
        result = s._expand_model_spec("dashscope:{llm_model}")
        self.assertEqual(result, f"dashscope:{s.llm_model}")

    def test_expand_model_spec_empty_field(self):
        from app.config import Settings
        s = Settings()
        # local_openai_model may be empty in some configs
        result = s._expand_model_spec("localopenai:{local_openai_model}")
        if s.local_openai_model:
            self.assertEqual(result, f"localopenai:{s.local_openai_model}")
        else:
            # Empty model → just provider name
            self.assertEqual(result, "localopenai")


class TestProviderConfigResolution(unittest.TestCase):
    """Test get_provider_config for built-in and custom providers."""

    def test_dashscope_provider_uses_openai_settings(self):
        from app.config import Settings
        s = Settings()
        config = s.get_provider_config("dashscope")
        self.assertEqual(config.base_url, s.openai_base_url)
        self.assertEqual(config.api_key, s.openai_api_key)

    def test_ollama_provider_appends_v1(self):
        from app.config import Settings
        s = Settings()
        config = s.get_provider_config("ollama")
        self.assertTrue(config.base_url.endswith("/v1"))
        self.assertEqual(config.api_key, "")  # No key needed

    def test_ollama_provider_does_not_double_append_v1(self):
        from app.config import Settings
        s = Settings()
        # If base_url already ends with /v1, don't append again
        s._temp_ollama_url = "http://localhost:11434/v1"
        with patch.object(s, "ollama_base_url", "http://localhost:11434/v1"):
            config = s.get_provider_config("ollama")
            self.assertTrue(config.base_url.endswith("/v1"))
            self.assertFalse(config.base_url.endswith("/v1/v1"))

    def test_localopenai_provider(self):
        from app.config import Settings
        s = Settings()
        config = s.get_provider_config("localopenai")
        self.assertEqual(config.base_url, s.local_openai_base_url)

    def test_custom_provider_from_env(self):
        from app.config import Settings
        s = Settings()
        with patch.dict(os.environ, {
            "LLM_PROVIDER_DEEPSEEK_BASE_URL": "https://api.deepseek.com/v1",
            "LLM_PROVIDER_DEEPSEEK_API_KEY": "sk-test-deepseek",
        }):
            config = s.get_provider_config("deepseek")
            self.assertEqual(config.base_url, "https://api.deepseek.com/v1")
            self.assertEqual(config.api_key, "sk-test-deepseek")

    def test_custom_provider_empty_when_no_env(self):
        from app.config import Settings
        s = Settings()
        # Clean env
        with patch.dict(os.environ, {}, clear=False):
            # Remove any existing keys
            for key in list(os.environ.keys()):
                if key.startswith("LLM_PROVIDER_NONEXISTENT"):
                    del os.environ[key]
            config = s.get_provider_config("nonexistent")
            self.assertEqual(config.base_url, "")
            self.assertEqual(config.api_key, "")

    def test_builtin_provider_env_override(self):
        from app.config import Settings
        s = Settings()
        with patch.dict(os.environ, {"LLM_PROVIDER_DASHSCOPE_API_KEY": "override-key"}):
            config = s.get_provider_config("dashscope")
            self.assertEqual(config.api_key, "override-key")


class TestRoleConfigResolution(unittest.TestCase):
    """Test that get_llm_config resolves role configurations correctly."""

    def test_chat_role_defaults_to_dashscope(self):
        from app.config import Settings
        s = Settings()
        config = s.get_llm_config("chat")
        self.assertEqual(config.provider, "dashscope")
        self.assertEqual(config.model, s.llm_model)
        self.assertEqual(config.base_url, s.openai_base_url)
        self.assertEqual(config.temperature, 0.5)

    def test_chat_routing_role_has_low_temperature(self):
        from app.config import Settings
        s = Settings()
        config = s.get_llm_config("chat_routing")
        self.assertEqual(config.temperature, 0.0)
        self.assertEqual(config.timeout, 30)
        self.assertEqual(config.max_tokens, 64)

    def test_knowledge_role_defaults_to_localopenai(self):
        from app.config import Settings
        s = Settings()
        config = s.get_llm_config("knowledge_distill")
        self.assertEqual(config.provider, "localopenai")
        self.assertEqual(config.temperature, 0.3)

    def test_role_model_spec_override(self):
        """LLM_KNOWLEDGE_DISTILL_MODEL=ollama:qwen3:35b overrides default."""
        from app.config import Settings
        s = Settings()
        with patch.dict(os.environ, {"LLM_KNOWLEDGE_DISTILL_MODEL": "ollama:qwen3:35b"}):
            config = s.get_llm_config("knowledge_distill")
            self.assertEqual(config.provider, "ollama")
            self.assertEqual(config.model, "qwen3:35b")

    def test_role_model_spec_with_custom_provider(self):
        """LLM_CHAT_MODEL=deepseek:deepseek-chat uses custom provider."""
        from app.config import Settings
        s = Settings()
        with patch.dict(os.environ, {
            "LLM_CHAT_MODEL": "deepseek:deepseek-chat",
            "LLM_PROVIDER_DEEPSEEK_BASE_URL": "https://api.deepseek.com/v1",
            "LLM_PROVIDER_DEEPSEEK_API_KEY": "sk-test",
        }):
            config = s.get_llm_config("chat")
            self.assertEqual(config.provider, "deepseek")
            self.assertEqual(config.model, "deepseek-chat")
            self.assertEqual(config.base_url, "https://api.deepseek.com/v1")
            self.assertEqual(config.api_key, "sk-test")

    def test_role_temperature_override(self):
        from app.config import Settings
        s = Settings()
        with patch.dict(os.environ, {"LLM_CHAT_TEMPERATURE": "0.1"}):
            config = s.get_llm_config("chat")
            self.assertEqual(config.temperature, 0.1)

    def test_unknown_role_falls_back_to_chat(self):
        from app.config import Settings
        s = Settings()
        config = s.get_llm_config("nonexistent_role")
        # Should use chat defaults
        self.assertEqual(config.temperature, 0.5)

    def test_rag_embedding_role_uses_dashscope(self):
        from app.config import Settings
        s = Settings()
        config = s.get_llm_config("rag_embedding")
        self.assertEqual(config.provider, "dashscope")
        self.assertEqual(config.model, s.embedding_model)
        self.assertEqual(config.temperature, 0.0)

    def test_model_with_colon_in_name(self):
        """Ollama model names like qwen3:35b should parse correctly."""
        from app.config import Settings
        s = Settings()
        with patch.dict(os.environ, {"LLM_CHAT_MODEL": "ollama:gemma4:e2b"}):
            config = s.get_llm_config("chat")
            self.assertEqual(config.provider, "ollama")
            self.assertEqual(config.model, "gemma4:e2b")

    def test_asr_correction_role_has_prompt(self):
        """asr_correction role should have its prompt expanded from settings."""
        from app.config import Settings
        s = Settings()
        config = s.get_llm_config("asr_correction")
        self.assertEqual(config.prompt, s.text_model_correction_prompt)

    def test_content_summary_role_has_prompt(self):
        """content_summary role should have its prompt expanded from settings."""
        from app.config import Settings
        s = Settings()
        config = s.get_llm_config("content_summary")
        self.assertEqual(config.prompt, s.text_model_summary_prompt)

    def test_role_without_prompt_has_empty_prompt(self):
        """Roles without a prompt field should have empty prompt."""
        from app.config import Settings
        s = Settings()
        config = s.get_llm_config("chat")
        self.assertEqual(config.prompt, "")


class TestFactoryCaching(unittest.TestCase):
    """Test that the factory caches instances per role."""

    def setUp(self):
        reset_factory()

    def tearDown(self):
        reset_factory()

    @patch("app.services.llm.factory._create_service")
    def test_same_role_returns_cached_instance(self, mock_create):
        mock_service = MagicMock()
        mock_create.return_value = mock_service
        result1 = get_llm_service("chat")
        result2 = get_llm_service("chat")
        self.assertIs(result1, result2)
        self.assertEqual(mock_create.call_count, 1)

    @patch("app.services.llm.factory._create_service")
    def test_different_roles_create_different_instances(self, mock_create):
        mock_chat = MagicMock()
        mock_rag = MagicMock()
        mock_create.side_effect = [mock_chat, mock_rag]
        result1 = get_llm_service("chat")
        result2 = get_llm_service("rag_qa")
        self.assertIsNot(result1, result2)
        self.assertEqual(mock_create.call_count, 2)

    def test_reset_factory_clears_cache(self):
        with patch("app.services.llm.factory._create_service") as mock_create:
            mock_create.return_value = MagicMock()
            get_llm_service("chat")
            self.assertEqual(mock_create.call_count, 1)
            reset_factory()
            get_llm_service("chat")
            self.assertEqual(mock_create.call_count, 2)


class TestGetLangchainChat(unittest.TestCase):
    """Test get_langchain_chat bridge method."""

    def setUp(self):
        reset_factory()

    def tearDown(self):
        reset_factory()

    @patch("app.services.llm.factory._create_service")
    def test_returns_langchain_chat_instance(self, mock_create):
        mock_service = MagicMock()
        mock_langchain = MagicMock()
        mock_service.as_langchain_chat.return_value = mock_langchain
        mock_create.return_value = mock_service

        result = get_langchain_chat("rag_qa")
        mock_service.as_langchain_chat.assert_called_once()
        self.assertEqual(result, mock_langchain)


class TestGetOpenAIClient(unittest.TestCase):
    """Test get_openai_client bridge method."""

    def setUp(self):
        reset_factory()

    def tearDown(self):
        reset_factory()

    @patch("app.services.llm.factory._create_service")
    def test_returns_sync_client(self, mock_create):
        mock_service = MagicMock()
        mock_client = MagicMock()
        mock_service.sync_client = mock_client
        mock_create.return_value = mock_service

        result = get_openai_client("chat")
        self.assertEqual(result, mock_client)


class TestGetEmbeddings(unittest.TestCase):
    """Test get_embeddings with DashScope vs OpenAI detection."""

    @patch("app.services.llm.factory._create_embeddings")
    @patch("app.services.llm.factory.settings")
    def test_embeddings_creation(self, mock_settings, mock_create):
        mock_config = LLMRoleConfig(
            provider="dashscope",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            api_key="test-key",
            model="text-embedding-v4",
        )
        mock_settings.get_llm_config.return_value = mock_config
        mock_embeddings = MagicMock()
        mock_create.return_value = mock_embeddings

        result = get_embeddings("rag_embedding")
        mock_create.assert_called_once_with(mock_config)


class TestOpenAICompatibleLLMService(unittest.TestCase):
    """Test the OpenAICompatibleLLMService implementation."""

    def test_lazy_client_initialization(self):
        from app.services.llm.openai_compatible import OpenAICompatibleLLMService
        service = OpenAICompatibleLLMService(
            base_url="http://localhost:11434/v1",
            api_key="test",
            model="gemma4:e2b",
        )
        self.assertIsNone(service._async_client)
        self.assertIsNone(service._sync_client)

    def test_async_client_property_creates_client(self):
        from app.services.llm.openai_compatible import OpenAICompatibleLLMService
        service = OpenAICompatibleLLMService(
            base_url="http://localhost:11434/v1",
            api_key="test",
            model="gemma4:e2b",
        )
        client = service.async_client
        self.assertIsNotNone(client)
        self.assertIs(service.async_client, client)

    def test_sync_client_property_creates_client(self):
        from app.services.llm.openai_compatible import OpenAICompatibleLLMService
        service = OpenAICompatibleLLMService(
            base_url="http://localhost:11434/v1",
            api_key="test",
            model="gemma4:e2b",
        )
        client = service.sync_client
        self.assertIsNotNone(client)
        self.assertIs(service.sync_client, client)


class TestChatCompletionPostProcessor(unittest.TestCase):
    """Test the backward-compatible ChatCompletionPostProcessor."""

    def test_implements_text_postprocessor_protocol(self):
        from app.services.text_postprocessor import TextPostProcessor
        processor = ChatCompletionPostProcessor(
            role="asr_correction",
            system_prompt="test prompt",
        )
        self.assertTrue(hasattr(processor, "postprocess"))
        self.assertTrue(callable(getattr(processor, "postprocess")))

    @patch("app.services.llm.postprocessor_adapter.get_llm_service")
    def test_postprocess_sends_system_and_user_messages(self, mock_get_service):
        mock_llm = AsyncMock()
        mock_llm.complete.return_value = LLMResponse(content="processed result")
        mock_get_service.return_value = mock_llm

        processor = ChatCompletionPostProcessor(
            role="asr_correction",
            system_prompt="Correct this text",
        )

        import asyncio
        result = asyncio.run(
            processor.postprocess("some text", title="My Title")
        )

        self.assertEqual(result, "processed result")
        mock_llm.complete.assert_called_once()
        call_args = mock_llm.complete.call_args
        messages = call_args[0][0]
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0].role, "system")
        self.assertIn("Correct this text", messages[0].content)
        self.assertIn("My Title", messages[0].content)  # title context is in system prompt
        self.assertEqual(messages[1].role, "user")
        self.assertIn("some text", messages[1].content)

    @patch("app.services.llm.postprocessor_adapter.get_llm_service")
    def test_postprocess_without_title(self, mock_get_service):
        mock_llm = AsyncMock()
        mock_llm.complete.return_value = LLMResponse(content="processed result")
        mock_get_service.return_value = mock_llm

        processor = ChatCompletionPostProcessor(
            role="asr_correction",
            system_prompt="Correct this text",
        )

        import asyncio
        result = asyncio.run(
            processor.postprocess("some text")
        )

        messages = mock_llm.complete.call_args[0][0]
        self.assertEqual(messages[1].content, "some text")


class TestCreateTextPostprocessorCompat(unittest.TestCase):
    """Test that create_text_postprocessor returns a ChatCompletionPostProcessor."""

    def test_returns_chat_completion_postprocessor(self):
        from app.services.text_postprocessor_factory import create_text_postprocessor

        result = create_text_postprocessor()
        self.assertIsInstance(result, ChatCompletionPostProcessor)

    def test_custom_prompt_template(self):
        from app.services.text_postprocessor_factory import create_text_postprocessor

        result = create_text_postprocessor(prompt_template="Custom prompt")
        self.assertIsInstance(result, ChatCompletionPostProcessor)
        self.assertEqual(result._system_prompt, "Custom prompt")


if __name__ == "__main__":
    unittest.main()
