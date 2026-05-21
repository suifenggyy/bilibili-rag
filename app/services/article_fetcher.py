"""
文章正文提取服务。

抓取链路：
    1. requests 下载 HTML + trafilatura 提取
    2. Playwright 渲染页面 + trafilatura 提取
    3. 仅保留标题 + URL
"""

import asyncio
import json
from typing import Optional

from loguru import logger

from app.services.content_summary import ContentSummaryService, append_summary_section
from app.services.content_storage import ContentStorageManager


class ArticleFetcher:
    """
    文章正文提取器。

    特性：
    - requests 首选抓取
    - Playwright 作为 JS 页面回退
    - 过滤“环境异常”和过短正文
    - 并发安全（每次调用独立会话）
    """

    FETCH_TIMEOUT = 20
    MIN_CONTENT_LENGTH = 200
    INVALID_TITLES = {"环境异常"}
    REQUEST_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36"
        ),
        "Referer": "https://mp.weixin.qq.com/",
    }
    PLAYWRIGHT_USER_AGENT = "Mozilla/5.0 Chrome/124"

    def __init__(
        self,
        storage_manager: Optional[ContentStorageManager] = None,
        summary_service: Optional[ContentSummaryService] = None,
    ):
        self._trafilatura_available = self._check_trafilatura()
        self._playwright_available = self._check_playwright()
        self.storage_manager = storage_manager or ContentStorageManager()
        self.summary_service = summary_service or ContentSummaryService()

    def _check_trafilatura(self) -> bool:
        try:
            import trafilatura  # noqa
            return True
        except ImportError:
            logger.warning(
                "[ArticleFetcher] trafilatura 未安装，正文提取将不可用。"
                "请运行：pip install trafilatura"
            )
            return False

    def _check_playwright(self) -> bool:
        try:
            import playwright.async_api  # noqa
            return True
        except ImportError:
            logger.warning(
                "[ArticleFetcher] playwright 未安装，动态页面回退将不可用。"
                "请运行：pip install playwright && playwright install chromium"
            )
            return False

    async def fetch_content(self, url: str, title: str = "") -> dict:
        """
        从 URL 提取文章正文。

        Returns:
            {
                "text":    str   正文（Markdown 格式，失败时为空字符串）
                "title":   str   页面标题（提取值，失败时回退到传入 title）
                "source":  str   "trafilatura" | "basic_info"
                "url":     str   原始 URL
            }
        """
        base_result = {
            "text": "",
            "title": title,
            "source": "basic_info",
            "url": url,
        }

        if not url or not url.startswith(("http://", "https://")):
            logger.warning(f"[ArticleFetcher] 无效 URL: {url}")
            self._persist_metadata(title or "未知标题", base_result)
            return base_result

        if not self._trafilatura_available:
            self._persist_metadata(title or "未知标题", base_result)
            return base_result

        text, extracted_title = await asyncio.to_thread(self._extract_with_requests, url)
        if self._is_valid_extraction(text, extracted_title):
            result = self._build_success_result(title, extracted_title, text, url)
            result["summary_block"] = await self._summarize_content(url, result["text"])
            logger.info(
                f"[ArticleFetcher] requests 提取成功: {len(result['text'])}chars, "
                f"title='{result['title'][:30]}', url={url[:50]}"
            )
            self._persist_result(title or extracted_title or "未知标题", result)
            return result

        if self._playwright_available:
            text, extracted_title = await self._extract_with_playwright(url)
            if self._is_valid_extraction(text, extracted_title):
                result = self._build_success_result(title, extracted_title, text, url)
                result["summary_block"] = await self._summarize_content(url, result["text"])
                logger.info(
                    f"[ArticleFetcher] playwright 提取成功: {len(result['text'])}chars, "
                    f"title='{result['title'][:30]}', url={url[:50]}"
                )
                self._persist_result(title or extracted_title or "未知标题", result)
                return result

            text, extracted_title = await self._extract_with_playwright_relaxed(url)
            if self._is_valid_extraction(text, extracted_title):
                result = self._build_success_result(title, extracted_title, text, url)
                result["summary_block"] = await self._summarize_content(url, result["text"])
                logger.info(
                    f"[ArticleFetcher] playwright 宽松提取成功: {len(result['text'])}chars, "
                    f"title='{result['title'][:30]}', url={url[:50]}"
                )
                self._persist_result(title or extracted_title or "未知标题", result)
                return result

        logger.info(f"[ArticleFetcher] trafilatura 提取失败，降级: {url[:60]}")
        self._persist_metadata(title or "未知标题", base_result)
        return base_result

    def _build_success_result(
        self, fallback_title: str, extracted_title: str, text: str, url: str
    ) -> dict:
        return {
            "text": text.strip(),
            "title": extracted_title or fallback_title,
            "source": "trafilatura",
            "url": url,
        }

    def _is_valid_extraction(self, text: str, extracted_title: str) -> bool:
        normalized_text = (text or "").strip()
        normalized_title = (extracted_title or "").strip()

        if not normalized_text:
            return False

        if normalized_title in self.INVALID_TITLES:
            logger.warning(
                f"[ArticleFetcher] 提取结果标题异常，视为失败: title='{normalized_title}'"
            )
            return False

        if len(normalized_text) < self.MIN_CONTENT_LENGTH:
            logger.warning(
                f"[ArticleFetcher] 提取结果正文过短，视为失败: {len(normalized_text)}chars"
            )
            return False

        return True

    async def _summarize_content(self, url: str, text: str) -> str:
        summary_service = getattr(self, "summary_service", None)
        cleaned = (text or "").strip()
        if not summary_service or not cleaned:
            return ""
        try:
            return await summary_service.summarize(cleaned)
        except Exception as e:
            logger.warning(f"[ArticleFetcher] 正文总结失败，跳过总结块: {e}, url={url[:60]}")
            return ""

    def _persist_result(self, title: str, content: dict) -> None:
        text = (content.get("text") or "").strip()
        if text:
            self.storage_manager.write_work_text("instapaper", title, "article_raw.md", text)
        self._persist_metadata(title, content)

    def _persist_metadata(self, title: str, content: dict) -> None:
        metadata = {
            "title": content.get("title") or title,
            "source": content.get("source", "basic_info"),
            "url": content.get("url", ""),
        }
        self.storage_manager.write_work_text(
            "instapaper",
            title,
            "metadata.json",
            json.dumps(metadata, ensure_ascii=False, indent=2),
        )

    def _extract_with_requests(self, url: str) -> tuple[str, str]:
        try:
            import requests
        except ImportError:
            logger.warning("[ArticleFetcher] requests 未安装，无法执行正文抓取")
            return "", ""

        try:
            response = requests.get(
                url,
                headers=self.REQUEST_HEADERS,
                timeout=self.FETCH_TIMEOUT,
            )
            response.raise_for_status()
            response.encoding = response.apparent_encoding
            return self._extract_from_html(response.text, url, "requests")
        except Exception as e:
            logger.warning(f"[ArticleFetcher] requests 抓取异常: {e}, url={url[:60]}")
            return "", ""

    async def _extract_with_playwright(self, url: str) -> tuple[str, str]:
        return await self._extract_with_playwright_mode(
            url,
            include_links=True,
            include_images=True,
        )

    async def _extract_with_playwright_relaxed(self, url: str) -> tuple[str, str]:
        return await self._extract_with_playwright_mode(
            url,
            include_links=True,
            include_images=True,
        )

    async def _extract_with_playwright_mode(
        self,
        url: str,
        *,
        include_links: bool,
        include_images: bool,
    ) -> tuple[str, str]:
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            logger.warning("[ArticleFetcher] playwright 未安装，无法执行浏览器回退抓取")
            return "", ""

        browser = None
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page(user_agent=self.PLAYWRIGHT_USER_AGENT)
                await page.goto(url, timeout=self.FETCH_TIMEOUT * 1000)
                html = await page.content()
            return await asyncio.to_thread(
                self._extract_from_html,
                html,
                url,
                "playwright",
                include_links,
                include_images,
            )
        except Exception as e:
            logger.warning(f"[ArticleFetcher] playwright 抓取异常: {e}, url={url[:60]}")
            return "", ""
        finally:
            if browser is not None:
                await browser.close()

    def _extract_from_html(
        self,
        html: str,
        url: str,
        method: str,
        include_links: bool = True,
        include_images: bool = True,
    ) -> tuple[str, str]:
        if not html:
            logger.warning(f"[ArticleFetcher] {method} 返回空 HTML: {url[:60]}")
            return "", ""

        try:
            import trafilatura

            text = trafilatura.extract(
                html,
                output_format="markdown",
                include_comments=False,
                include_tables=True,
                include_links=include_links,
                include_images=include_images,
                no_fallback=False,
                favor_recall=True,
            )
            meta = trafilatura.extract_metadata(html)
            extracted_title = (meta.title if meta and meta.title else "") or ""
            return (text or "").strip(), extracted_title
        except Exception as e:
            logger.warning(f"[ArticleFetcher] {method} 提取异常: {e}, url={url[:60]}")
            return "", ""

    @staticmethod
    def build_markdown(bookmark: dict, content: dict) -> str:
        """
        将书签元数据 + 提取的正文拼装为 Markdown 文件内容。

        Args:
            bookmark: Instapaper API 返回的书签 dict
            content:  fetch_content() 返回的 dict
        """
        from datetime import datetime

        title = content.get("title") or bookmark.get("title") or "未知标题"
        url = bookmark.get("url") or content.get("url") or ""
        description = bookmark.get("description") or ""
        bm_time = bookmark.get("time") or 0
        source = content.get("source", "basic_info")

        saved_str = (
            datetime.fromtimestamp(bm_time).strftime("%Y-%m-%d")
            if bm_time else "未知"
        )
        source_label = {
            "trafilatura": "trafilatura 自动提取",
            "basic_info": "基本信息（正文提取失败）",
        }.get(source, source)

        lines = [
            f"# {title}",
            "",
            "## 文章信息",
            "",
            "| 字段 | 内容 |",
            "|------|------|",
            f"| 来源 URL | [{url}]({url}) |",
            f"| 保存日期 | {saved_str} |",
            f"| 内容来源 | {source_label} |",
        ]

        if description:
            lines += ["", "**摘要：** " + description]

        append_summary_section(lines, content.get("summary_block", ""))
        lines += ["", "---", "", "## 正文", ""]

        text = content.get("text", "").strip()
        if text:
            lines.append(text)
        else:
            lines.append("_（正文提取失败，请访问原始链接查看）_")
            lines.append(f"\n原文链接：{url}")

        lines += [
            "",
            "---",
            "",
            f"_导出时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}_",
        ]

        return "\n".join(lines)
