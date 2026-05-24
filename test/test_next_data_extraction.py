"""
测试 ArticleFetcher 的 __NEXT_DATA__ 提取功能。

验证：
1. _extract_from_next_data 能从 Next.js 页面（如 Longbridge）提取正文
2. fetch_content 优先走 __NEXT_DATA__ 路径
3. 非 Next.js 页面不受影响（graceful fallback）
"""

import asyncio
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from app.services.article_fetcher import ArticleFetcher
from app.services.content_storage import ContentStorageManager


def _make_fetcher() -> ArticleFetcher:
    storage = MagicMock(spec=ContentStorageManager)
    storage.write_work_text = MagicMock()
    storage.get_export_dir = MagicMock(return_value=Path("/tmp"))
    return ArticleFetcher(storage_manager=storage)


# ── HTML fixtures ─────────────────────────────────────────────────────────────

def _make_next_html(body: str, title: str = "测试文章标题") -> str:
    """构造包含 __NEXT_DATA__ 的 Next.js HTML。"""
    payload = json.dumps({
        "props": {
            "pageProps": {
                "details": {
                    "title": title,
                    "body": body,
                    "description": "摘要文字",
                }
            }
        },
        "page": "/news/[id]",
    }, ensure_ascii=False)
    return f"""<!DOCTYPE html>
<html><head><title>{title}</title></head>
<body>
<div id="__NEXT_DATA__" style="display:none">该内容在当前地区不可见</div>
<script id="__NEXT_DATA__" type="application/json">{payload}</script>
</body></html>"""


def _make_plain_html(body: str, title: str = "普通页面标题") -> str:
    """构造不含 __NEXT_DATA__ 的普通 HTML。"""
    return f"""<!DOCTYPE html>
<html><head><title>{title}</title></head>
<body><article>{body}</article></body></html>"""


LONG_ARTICLE = (
    "这是一篇关于人工智能发展的深度文章。"
    "近年来，大语言模型（LLM）技术取得了突破性进展，"
    "从 GPT-3 到 GPT-4，再到开源的 LLaMA 系列，"
    "模型能力持续提升，应用场景不断拓宽。"
    "本文将从技术、产业和社会影响三个维度进行分析。" * 8
)


# ── 单元测试 ──────────────────────────────────────────────────────────────────

class TestNextDataExtraction(unittest.TestCase):
    """_extract_from_next_data 的单元测试（mock HTTP 请求）。"""

    def setUp(self):
        self.fetcher = _make_fetcher()

    def _mock_requests(self, html: str):
        """返回 patch context，让 requests.get 返回 mock HTML。"""
        mock_resp = MagicMock()
        mock_resp.text = html
        mock_resp.raise_for_status = MagicMock()
        mock_resp.apparent_encoding = "utf-8"
        return patch("requests.get", return_value=mock_resp)

    # ── 正常提取 ──────────────────────────────────────────────────────────────

    def test_extracts_body_from_next_data(self):
        html = _make_next_html(f"<p>{LONG_ARTICLE}</p>", "SpaceX 招股书揭秘")
        with self._mock_requests(html):
            text, title = self.fetcher._extract_from_next_data("https://example.com/news/1")
        self.assertGreater(len(text), 200, "应提取到足够长的正文")
        self.assertIn("大语言模型", text)
        self.assertEqual(title, "SpaceX 招股书揭秘")

    def test_extracts_html_body_as_markdown(self):
        """Body 含 HTML 标签时应转换为 Markdown。"""
        body = f"<h2>核心摘要</h2><p><strong>重点：</strong>{LONG_ARTICLE}</p>"
        html = _make_next_html(body)
        with self._mock_requests(html):
            text, _ = self.fetcher._extract_from_next_data("https://example.com/news/2")
        self.assertGreater(len(text), 200)
        self.assertIn("重点", text)

    def test_inline_images_preserve_position(self):
        """行内 <img> 应出现在对应段落附近，而非全部堆到末尾。"""
        img1 = "https://cdn.example.com/img1.png"
        img2 = "https://cdn.example.com/img2.png"
        body = (
            f"<p>第一段文字{'X' * 50}<img src='{img1}' alt='图一'/>更多内容{'Y' * 50}</p>"
            f"<p>第二段文字{'A' * 50}</p>"
            f"<p>第三段文字{'B' * 50}<img src='{img2}' alt='图二'/>结尾{'C' * 50}</p>"
        )
        html = _make_next_html(body)
        with self._mock_requests(html):
            text, _ = self.fetcher._extract_from_next_data("https://example.com/news/imgs")
        # 两张图片都出现在文本中
        self.assertIn(img1, text)
        self.assertIn(img2, text)
        # img1 应在 img2 之前（保持原始顺序）
        self.assertLess(text.index(img1), text.index(img2))

    def test_nested_next_data_structure(self):
        """支持深层嵌套的 __NEXT_DATA__ 结构。"""
        payload = json.dumps({
            "props": {
                "pageProps": {
                    "article": {
                        "meta": {"author": "张三"},
                        "content": {
                            "title": "嵌套标题",
                            "body": f"<p>{LONG_ARTICLE}</p>",
                        }
                    }
                }
            }
        }, ensure_ascii=False)
        html = f"""<html><head></head><body>
<script id="__NEXT_DATA__" type="application/json">{payload}</script>
</body></html>"""
        with self._mock_requests(html):
            text, title = self.fetcher._extract_from_next_data("https://example.com/news/3")
        self.assertGreater(len(text), 200)
        self.assertEqual(title, "嵌套标题")

    # ── 边界情况 ──────────────────────────────────────────────────────────────

    def test_returns_empty_when_no_next_data(self):
        html = _make_plain_html(f"<p>{LONG_ARTICLE}</p>")
        with self._mock_requests(html):
            text, title = self.fetcher._extract_from_next_data("https://example.com/plain")
        self.assertEqual(text, "")
        self.assertEqual(title, "")

    def test_returns_empty_when_body_too_short(self):
        html = _make_next_html("<p>内容太短</p>")
        with self._mock_requests(html):
            text, title = self.fetcher._extract_from_next_data("https://example.com/short")
        self.assertEqual(text, "")  # body 字段 < 200 chars，不被识别

    def test_returns_empty_on_http_error(self):
        with patch("requests.get", side_effect=Exception("连接超时")):
            text, title = self.fetcher._extract_from_next_data("https://example.com/err")
        self.assertEqual(text, "")
        self.assertEqual(title, "")

    def test_returns_empty_on_invalid_json(self):
        html = """<html><body>
<script id="__NEXT_DATA__" type="application/json">{invalid json</script>
</body></html>"""
        with self._mock_requests(html):
            text, title = self.fetcher._extract_from_next_data("https://example.com/badjson")
        self.assertEqual(text, "")


# ── 集成测试（mock 完整 fetch_content 流程） ─────────────────────────────────

class TestFetchContentNextDataPriority(unittest.IsolatedAsyncioTestCase):
    """验证 fetch_content 优先走 __NEXT_DATA__ 路径。"""

    def setUp(self):
        self.fetcher = _make_fetcher()

    async def test_next_data_takes_priority_over_trafilatura(self):
        """__NEXT_DATA__ 成功时不再调用 requests/playwright 通用路径。"""
        long_body = f"<p>{LONG_ARTICLE}</p>"
        html = _make_next_html(long_body, "优先级测试")

        mock_resp = MagicMock()
        mock_resp.text = html
        mock_resp.raise_for_status = MagicMock()

        with patch("requests.get", return_value=mock_resp) as mock_get:
            result = await self.fetcher.fetch_content(
                "https://example.com/news/priority", "优先级测试"
            )

        self.assertEqual(result["source"], "next_data")
        self.assertGreater(len(result["text"]), 200)
        self.assertEqual(result["title"], "优先级测试")
        # requests.get 只调用一次（__NEXT_DATA__ 内部）
        self.assertEqual(mock_get.call_count, 1)

    async def test_fallback_to_requests_when_no_next_data(self):
        """无 __NEXT_DATA__ 时回退到普通 requests + trafilatura 路径。"""
        long_body = LONG_ARTICLE * 3
        plain_html = _make_plain_html(f"<p>{long_body}</p>", "普通文章")

        mock_resp = MagicMock()
        mock_resp.text = plain_html
        mock_resp.raise_for_status = MagicMock()
        mock_resp.apparent_encoding = "utf-8"

        with patch("requests.get", return_value=mock_resp):
            result = await self.fetcher.fetch_content(
                "https://example.com/plain", "普通文章"
            )

        # 无 __NEXT_DATA__，source 不是 next_data
        self.assertNotEqual(result["source"], "next_data")

    async def test_invalid_url_returns_basic_info(self):
        result = await self.fetcher.fetch_content("not-a-url", "无效链接")
        self.assertEqual(result["source"], "basic_info")
        self.assertEqual(result["text"], "")


# ── 可选：真实网络测试（默认跳过）────────────────────────────────────────────

@unittest.skip("需要网络访问，手动运行：python -m pytest -k real_network -s")
class TestRealNetwork(unittest.IsolatedAsyncioTestCase):
    async def test_longbridge_article(self):
        fetcher = _make_fetcher()
        result = await fetcher.fetch_content(
            "https://longbridge.com/zh-CN/news/287124673",
            "SpaceX 招股书揭秘"
        )
        print(f"\nsource: {result['source']}")
        print(f"title: {result['title']}")
        print(f"text ({len(result['text'])} chars):\n{result['text'][:500]}")
        self.assertEqual(result["source"], "next_data")
        self.assertGreater(len(result["text"]), 500)


if __name__ == "__main__":
    unittest.main(verbosity=2)
