"""分块摘要汇总逻辑测试。

验证 content_summary 角色在文本超阈值分块后，会：
  1. 对每块各发一次摘要请求；
  2. 拼接所有块摘要后，再发一次汇总摘要请求；
  3. 最终返回汇总摘要（而非按纠错的重叠去重合并结果）。

同时验证 asr_correction 角色仍走原有的 _merge_corrected_chunks 合并路径。
"""

import asyncio
import unittest
from unittest.mock import patch

import app.services.llm.postprocessor_adapter as pp_adapter
from app.services.llm.postprocessor_adapter import ChatCompletionPostProcessor
from app.services.llm.types import LLMResponse


class _FakeLLM:
    """记录每次 complete 调用并按预设序列返回内容。"""

    def __init__(self, responses):
        # responses: list[str]，按调用顺序依次返回
        self._responses = list(responses)
        self.calls = []  # 记录每次调用的 user content

    async def complete(self, messages, **kwargs):
        user_content = next(
            (m.content for m in messages if m.role == "user"), ""
        )
        self.calls.append(user_content)
        content = self._responses.pop(0) if self._responses else ""
        return LLMResponse(content=content, model="fake", usage={})


class SummaryConsolidationTests(unittest.TestCase):
    """content_summary 分块 → 汇总流程。"""

    def test_summary_chunks_then_consolidate(self):
        processor = ChatCompletionPostProcessor(
            role="content_summary", system_prompt="SUMMARY PROMPT"
        )
        # 三块分别摘要 + 一次汇总 = 4 次调用
        fake = _FakeLLM([
            "summary: |\n  块1总结\nkey_points:\n  - \"[主旨] A\"\ntags:\n  - t1",
            "summary: |\n  块2总结\nkey_points:\n  - \"[主旨] B\"\ntags:\n  - t2",
            "summary: |\n  块3总结\nkey_points:\n  - \"[主旨] C\"\ntags:\n  - t3",
            "summary: |\n  汇总总结\nkey_points:\n  - \"[主旨] 汇总\"\ntags:\n  - t汇总",
        ])
        processor._llm = fake  # 绕过 _get_llm 的真实工厂

        # 构造恰好切成 3 块的文本：每块一句，chunk_size 取介于一句与两句之间。
        # 一句 = "段一内容内容内容内容内容内容内容内容内容内容。"（约 21 字符），取 chunk_size=25。
        long_text = (
            "段一内容内容内容内容内容内容内容内容内容内容内容内容内容内容内容内容内容内容内容内容内容内容。"
            "段二内容内容内容内容内容内容内容内容内容内容内容内容内容内容内容内容内容内容内容内容内容内容。"
            "段三内容内容内容内容内容内容内容内容内容内容内容内容内容内容内容内容内容内容内容内容内容内容。"
        )
        with patch.object(pp_adapter.settings, "text_model_summary_chunk_size", 50):
            result = asyncio.run(processor.postprocess(long_text))

        # 末次调用应是汇总请求，且其输入包含所有块摘要
        self.assertEqual(len(fake.calls), 4)
        final_call = fake.calls[-1]
        self.assertIn("块1总结", final_call)
        self.assertIn("块2总结", final_call)
        self.assertIn("块3总结", final_call)
        # 汇总请求的指令关键词
        self.assertIn("合并", final_call)
        # 最终返回的是汇总摘要
        self.assertIn("汇总总结", result)
        self.assertNotIn("块1总结", result)

    def test_single_chunk_summary_no_consolidation(self):
        """短文本单块时不应发起额外的汇总请求。"""
        processor = ChatCompletionPostProcessor(
            role="content_summary", system_prompt="SUMMARY PROMPT"
        )
        fake = _FakeLLM([
            "summary: |\n  唯一总结\nkey_points:\n  - \"[主旨] A\"\ntags:\n  - t1",
        ])
        processor._llm = fake

        with patch.object(pp_adapter.settings, "text_model_summary_chunk_size", 100000):
            result = asyncio.run(processor.postprocess("短文本内容，不会触发分块。"))

        # 只调用一次，无汇总
        self.assertEqual(len(fake.calls), 1)
        self.assertIn("唯一总结", result)


class CorrectionMergePathTests(unittest.TestCase):
    """asr_correction 角色仍走原有重叠去重合并，不走汇总。"""

    def test_correction_uses_merge_not_consolidate(self):
        processor = ChatCompletionPostProcessor(
            role="asr_correction", system_prompt="CORRECTION PROMPT"
        )
        # 两块纠错，不应有第三次的"汇总"调用
        fake = _FakeLLM([
            "块一纠错结果结尾。",
            "块二纠错结果。",
        ])
        processor._llm = fake

        long_text = (
            "段一内容内容内容内容内容内容内容内容内容内容内容内容内容内容内容内容内容内容内容内容内容内容。"
            "段二内容内容内容内容内容内容内容内容内容内容内容内容内容内容内容内容内容内容内容内容内容内容。"
        )
        with patch.object(pp_adapter.settings, "text_model_correction_chunk_size", 50):
            result = asyncio.run(processor.postprocess(long_text))

        # 仅两次调用（每块一次），无汇总请求
        self.assertEqual(len(fake.calls), 2)
        self.assertIsInstance(result, str)


if __name__ == "__main__":
    unittest.main()
