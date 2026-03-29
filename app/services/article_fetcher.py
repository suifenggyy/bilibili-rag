"""
文章正文提取服务

使用 trafilatura 从原始 URL 提取文章正文，作为 Instapaper Premium get_text 的免费替代方案。
支持多级降级策略，确保任何情况下都有输出。

降级顺序：
    1. trafilatura  - 精度最高，支持 Markdown 输出
    2. 仅保留标题 + URL（trafilatura 提取失败时）
"""

import asyncio
import time
from typing import Optional
from urllib.parse import urlparse

import httpx
from loguru import logger


class ArticleFetcher:
    """
    文章正文提取器，基于 trafilatura。

    特性：
    - 自动处理重定向
    - 提取 Markdown 格式正文（保留标题/列表/代码块结构）
    - 付费墙/JS渲染页面优雅降级
    - 并发安全（每次调用独立的 httpx 会话）
    """

    # 请求超时（秒）
    FETCH_TIMEOUT = 30
    # 最大重试次数
    MAX_RETRIES = 2

    def __init__(self):
        self._trafilatura_available = self._check_trafilatura()

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

    async def fetch_content(self, url: str, title: str = "") -> dict:
        """
        从 URL 提取文章正文。

        Returns:
            {
                "text":    str   正文（Markdown 格式，失败时为空字符串）
                "title":   str   页面标题（trafilatura 提取，回退到传入的 title）
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
            return base_result

        if not self._trafilatura_available:
            return base_result

        # 使用 trafilatura 提取
        text, extracted_title = await asyncio.to_thread(
            self._extract_sync, url
        )

        if text:
            return {
                "text": text,
                "title": extracted_title or title,
                "source": "trafilatura",
                "url": url,
            }

        logger.info(f"[ArticleFetcher] trafilatura 提取失败，降级: {url[:60]}")
        return base_result

    def _extract_sync(self, url: str) -> tuple[str, str]:
        """
        同步执行 trafilatura 提取（在线程池中运行）。

        Returns:
            (text, title) 元组，失败时均为空字符串
        """
        try:
            import trafilatura

            # 使用 trafilatura 内置的下载器（带 User-Agent 和重试）
            downloaded = trafilatura.fetch_url(url)
            if not downloaded:
                logger.debug(f"[ArticleFetcher] fetch_url 返回空: {url[:60]}")
                return "", ""

            # 提取正文为 Markdown
            text = trafilatura.extract(
                downloaded,
                output_format="markdown",
                include_comments=False,
                include_tables=True,
                no_fallback=False,       # 允许降级到次要内容区域
                favor_recall=True,       # 优先召回（减少漏提取）
            )

            # 同时提取元数据（标题）
            meta = trafilatura.extract_metadata(downloaded)
            extracted_title = (meta.title if meta and meta.title else "") or ""

            if not text or len(text.strip()) < 50:
                logger.debug(
                    f"[ArticleFetcher] 提取内容过短({len(text or '')}chars): {url[:60]}"
                )
                return "", extracted_title

            logger.info(
                f"[ArticleFetcher] 提取成功: {len(text)}chars, "
                f"title='{extracted_title[:30]}', url={url[:50]}"
            )
            return text.strip(), extracted_title

        except Exception as e:
            logger.warning(f"[ArticleFetcher] 提取异常: {e}, url={url[:60]}")
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
