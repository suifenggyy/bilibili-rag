import unittest

from app.services.knowledge_pipeline.llm_processor import (
    _parse_summary_output,
    _parse_yaml_output,
    _try_parse_dict,
)


class TryParseDictTests(unittest.TestCase):
    def test_parses_yaml_dict(self):
        result = _try_parse_dict('summary: "hello"\nconcepts: []')
        self.assertEqual(result, {"summary": "hello", "concepts": []})

    def test_parses_json_dict(self):
        result = _try_parse_dict('{"summary": "hello"}')
        self.assertEqual(result, {"summary": "hello"})

    def test_strips_yaml_code_fence(self):
        result = _try_parse_dict("```yaml\nsummary: hi\n```")
        self.assertEqual(result, {"summary": "hi"})

    def test_returns_none_for_markdown(self):
        # Markdown starting with ## is a YAML comment → not a dict.
        self.assertIsNone(_try_parse_dict("## 概览\n这是正文。"))

    def test_returns_none_for_empty(self):
        self.assertIsNone(_try_parse_dict(""))
        self.assertIsNone(_try_parse_dict("   "))


class ParseYamlOutputTests(unittest.TestCase):
    def test_raises_on_markdown(self):
        with self.assertRaises(ValueError):
            _parse_yaml_output("## 概览\n这是正文。")

    def test_returns_dict_on_yaml(self):
        self.assertEqual(_parse_yaml_output("summary: hi"), {"summary": "hi"})


class ParseSummaryOutputTests(unittest.TestCase):
    def test_raw_markdown_becomes_summary(self):
        # The common case: prompt asks for markdown, model obeys.
        md = "## 概览\nHarness Engineering 围绕模型外部运行环境进行系统化设计。\n## 核心框架\n..."
        result = _parse_summary_output(md)
        self.assertEqual(result, {"summary": md})

    def test_markdown_wrapped_in_code_fence_is_unwrapped(self):
        md = "## 概览\n正文内容。"
        result = _parse_summary_output(f"```markdown\n{md}\n```")
        self.assertEqual(result, {"summary": md})

    def test_yaml_dict_with_summary_field_is_used(self):
        # If the model wraps the summary in a YAML dict, extract it.
        result = _parse_summary_output('summary: "这是概览"')
        self.assertEqual(result, {"summary": "这是概览"})

    def test_json_dict_with_summary_field_is_used(self):
        result = _parse_summary_output('{"summary": "这是概览"}')
        self.assertEqual(result, {"summary": "这是概览"})

    def test_empty_output_returns_empty_summary(self):
        self.assertEqual(_parse_summary_output(""), {"summary": ""})
        self.assertEqual(_parse_summary_output("   \n"), {"summary": ""})

    def test_never_raises_on_garbage(self):
        # Anything that isn't a usable dict falls back to raw text.
        result = _parse_summary_output("随机非结构化文字 without headings")
        self.assertEqual(result, {"summary": "随机非结构化文字 without headings"})


if __name__ == "__main__":
    unittest.main()
