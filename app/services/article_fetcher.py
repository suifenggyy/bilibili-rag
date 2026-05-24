"""
文章正文提取服务。

抓取链路：
    1. __NEXT_DATA__ JSON 提取（Next.js SPA，包含 geo-block 页面）
    2. requests 下载 HTML + trafilatura 提取
    3. Playwright 渲染页面 + trafilatura 提取
    4. 仅保留标题 + URL
"""

import asyncio
import json
import re
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

        # ── 优先：__NEXT_DATA__ 提取（处理 geo-block 的 Next.js 页面） ──
        text, extracted_title = await asyncio.to_thread(self._extract_from_next_data, url)
        if self._is_valid_extraction(text, extracted_title):
            result = self._build_success_result(title, extracted_title, text, url)
            result["source"] = "next_data"
            result["summary_block"] = await self._summarize_content(url, result["text"])
            logger.info(
                f"[ArticleFetcher] __NEXT_DATA__ 提取成功: {len(result['text'])}chars, "
                f"title='{result['title'][:30]}', url={url[:50]}"
            )
            self._persist_result(title or extracted_title or "未知标题", result)
            return result

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

    def _extract_from_next_data(self, url: str) -> tuple[str, str]:
        """从 Next.js __NEXT_DATA__ JSON 提取正文（适用于 geo-block 等渲染屏蔽场景）。"""
        try:
            import requests
            from html.parser import HTMLParser
        except ImportError:
            return "", ""

        try:
            resp = requests.get(url, headers=self.REQUEST_HEADERS, timeout=self.FETCH_TIMEOUT)
            resp.raise_for_status()
            html = resp.text
        except Exception as e:
            logger.debug(f"[ArticleFetcher] __NEXT_DATA__ 请求失败: {e}")
            return "", ""

        # 提取 __NEXT_DATA__ JSON
        m = re.search(
            r'<script\s+id="__NEXT_DATA__"\s+type="application/json">(.*?)</script>',
            html, re.S
        )
        if not m:
            return "", ""

        try:
            data = json.loads(m.group(1))
        except Exception:
            return "", ""

        # 递归搜索包含正文 HTML 的字段
        BODY_KEYS = ("body", "content", "articleBody", "text", "html", "richText")
        TITLE_KEYS = ("title", "headline", "name")
        # 独立图片字段（封面 / 图片列表）
        IMAGE_KEYS = ("image", "images", "cover", "coverImage", "thumbnail", "banner",
                      "featuredImage", "pic", "picUrl", "imgUrl")
        found_body: str = ""
        found_title: str = ""
        found_images: list[str] = []

        def _search(obj, depth: int = 0) -> None:
            nonlocal found_body, found_title
            if depth > 8:
                return
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if not found_title and k in TITLE_KEYS and isinstance(v, str) and len(v) > 2:
                        found_title = v
                    if not found_body and k in BODY_KEYS and isinstance(v, str) and len(v) > 200:
                        found_body = v
                    # 收集独立图片 URL
                    if k in IMAGE_KEYS:
                        if isinstance(v, str) and v.startswith("http"):
                            found_images.append(v)
                        elif isinstance(v, list):
                            for img in v:
                                if isinstance(img, str) and img.startswith("http"):
                                    found_images.append(img)
                                elif isinstance(img, dict):
                                    for url_key in ("url", "src", "href", "path"):
                                        if url_key in img and isinstance(img[url_key], str):
                                            found_images.append(img[url_key])
                                            break
                    _search(v, depth + 1)
            elif isinstance(obj, list):
                for item in obj:
                    _search(item, depth + 1)

        _search(data.get("props", {}))

        if not found_body:
            return "", ""

        # ── 保留行内图片位置：用占位符替换 <img>，trafilatura 处理后还原 ──
        # trafilatura 会把图片收集到末尾；改为先用占位文本顶住位置，
        # 再把占位符换回 ![alt](src) Markdown，保持原始排列顺序。
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(found_body, "html.parser")
            img_map: dict[str, str] = {}  # placeholder → markdown
            for i, tag in enumerate(soup.find_all("img")):
                src = tag.get("src", "")
                alt = tag.get("alt", "")
                if not src:
                    continue
                placeholder = f"IMGPLACEHOLDER{i}END"
                img_map[placeholder] = f"![{alt}]({src})"
                tag.replace_with(soup.new_string(f" {placeholder} "))
            processed_html = str(soup)
        except Exception:
            processed_html = found_body
            img_map = {}

        try:
            import trafilatura
            text = trafilatura.extract(
                f"<html><body>{processed_html}</body></html>",
                output_format="markdown",
                include_comments=False,
                include_tables=True,
                include_links=True,
                include_images=False,   # 行内图片已用占位符处理
                favor_precision=False,
            ) or ""
        except Exception:
            text = re.sub(r"<[^>]+>", " ", processed_html)
            text = re.sub(r"\s+", " ", text).strip()

        # 把占位符还原为 Markdown 图片（保持原始位置）
        for placeholder, md_img in img_map.items():
            text = text.replace(placeholder, md_img)

        # 追加独立图片字段（封面/缩略图等，去重，排除正文中已有的 URL）
        extra_imgs = [u for u in dict.fromkeys(found_images) if u not in text]
        if extra_imgs:
            img_md = "\n".join(f"![]({u})" for u in extra_imgs)
            text = f"{text}\n\n{img_md}" if text else img_md

        return text, found_title

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
