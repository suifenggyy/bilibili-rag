"""
抖音爬虫桥接模块

直接调用 douyin_tiktok_download_api 子模块（Evil0ctal/Douyin_TikTok_Download_API）
的 Python 函数，无需启动外部 HTTP 服务。

子模块路径: <project_root>/douyin_tiktok_download_api/
"""

import sys
from pathlib import Path

from loguru import logger

# Add submodule root to sys.path so `crawlers.*` imports resolve.
# Appended (not prepended) to avoid shadowing our own `app` package.
_SUBMODULE_ROOT = Path(__file__).resolve().parents[2] / "douyin_tiktok_download_api"
_SUBMODULE_ROOT_STR = str(_SUBMODULE_ROOT)
if _SUBMODULE_ROOT_STR not in sys.path:
    sys.path.append(_SUBMODULE_ROOT_STR)

# Lazy import to defer YAML config loading and heavy imports until first use.
_crawler_instance = None


def _get_crawler():
    """Return a shared DouyinWebCrawler instance (lazy-initialized)."""
    global _crawler_instance
    if _crawler_instance is None:
        from crawlers.douyin.web.web_crawler import DouyinWebCrawler  # noqa: PLC0415
        _crawler_instance = DouyinWebCrawler()
        logger.info("[DouyinBridge] DouyinWebCrawler 初始化完成（子模块直接调用模式）")
    return _crawler_instance


def is_available() -> bool:
    """Check that the submodule is importable (deps installed, files present)."""
    try:
        _get_crawler()
        return True
    except Exception as e:
        logger.warning(f"[DouyinBridge] 子模块不可用: {e}")
        return False


async def fetch_collection_videos(cookie: str, cursor: int = 0, count: int = 20) -> dict:
    """
    获取抖音收藏夹一页视频（直接调用子模块）。

    Args:
        cookie: 用户浏览器 Cookie 字符串
        cursor: 分页游标
        count:  每页数量

    Returns:
        原始 Douyin API 响应 dict，例如：
        {"aweme_list": [...], "has_more": 1, "max_cursor": 12345, "status_code": 0}
    """
    crawler = _get_crawler()
    result = await crawler.fetch_user_collection_videos(cookie=cookie, cursor=cursor, count=count)
    if result is None:
        return {}
    return result


async def fetch_user_post_videos(sec_user_id: str, max_cursor: int = 0, count: int = 20) -> dict:
    """
    获取抖音用户投稿视频一页（直接调用子模块）。

    Note: 使用子模块 config.yaml 中配置的 Cookie，不接受运行时传入。

    Returns:
        原始 Douyin API 响应 dict。
    """
    crawler = _get_crawler()
    result = await crawler.fetch_user_post_videos(
        sec_user_id=sec_user_id, max_cursor=max_cursor, count=count
    )
    if result is None:
        return {}
    return result
