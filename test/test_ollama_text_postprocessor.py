import unittest
from unittest.mock import AsyncMock, MagicMock, patch


class OllamaTextPostProcessorTests(unittest.IsolatedAsyncioTestCase):
    async def test_postprocess_calls_ollama_generate_and_returns_trimmed_text(self):
        from app.services.ollama_text_postprocessor import OllamaTextPostProcessor

        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"response": " 修正后的文本。\n"}

        client = AsyncMock()
        client.post = AsyncMock(return_value=response)

        async_client = AsyncMock()
        async_client.__aenter__.return_value = client
        async_client.__aexit__.return_value = None

        with patch(
            "app.services.ollama_text_postprocessor.httpx.AsyncClient",
            return_value=async_client,
        ) as async_client_cls:
            processor = OllamaTextPostProcessor(
                base_url="http://localhost:11434",
                model="gemma4:e2b",
                prompt_template="下面是一个语音转换生成的文本，修改其中识别错误的字，并完善格式，增加标点符号和段落;",
                timeout=45,
            )
            text = await processor.postprocess("原始 asr 文本")

        self.assertEqual(text, "修正后的文本。")
        async_client_cls.assert_called_once_with(timeout=45)
        client.post.assert_awaited_once_with(
            "http://localhost:11434/api/generate",
            json={
                "model": "gemma4:e2b",
                "prompt": "下面是一个语音转换生成的文本，修改其中识别错误的字，并完善格式，增加标点符号和段落;\n\n原始 asr 文本",
                "stream": False,
            },
        )


class ProxyTextPostProcessorTests(unittest.IsolatedAsyncioTestCase):
    async def test_postprocess_calls_openai_compatible_chat_completions(self):
        from app.services.proxy_text_postprocessor import ProxyTextPostProcessor

        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": " 代理修正后的文本。 \n",
                    }
                }
            ]
        }

        client = AsyncMock()
        client.post = AsyncMock(return_value=response)

        async_client = AsyncMock()
        async_client.__aenter__.return_value = client
        async_client.__aexit__.return_value = None

        with patch(
            "app.services.proxy_text_postprocessor.httpx.AsyncClient",
            return_value=async_client,
        ) as async_client_cls:
            processor = ProxyTextPostProcessor(
                base_url="http://localhost:4141",
                model="qwen-local",
                prompt_template="请纠错",
                timeout=90,
            )
            text = await processor.postprocess("原始正文")

        self.assertEqual(text, "代理修正后的文本。")
        async_client_cls.assert_called_once_with(timeout=90)
        client.post.assert_awaited_once_with(
            "http://localhost:4141/v1/chat/completions",
            json={
                "model": "qwen-local",
                "messages": [
                    {"role": "system", "content": "请纠错"},
                    {"role": "user", "content": "原始正文"},
                ],
            },
        )


if __name__ == "__main__":
    unittest.main()
