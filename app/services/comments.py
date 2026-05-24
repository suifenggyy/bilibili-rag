"""
评论抓取与格式化工具

支持 B站 和 抖音两个平台的热门评论获取，抓取失败时静默跳过（非阻断性）。
"""
import sys
from pathlib import Path
from typing import Optional

import httpx
from loguru import logger

# Ensure submodule is on sys.path (mirrors douyin_bridge.py setup)
_SUBMODULE_ROOT = Path(__file__).resolve().parents[2] / "douyin_tiktok_download_api"
_SUBMODULE_ROOT_STR = str(_SUBMODULE_ROOT)
if _SUBMODULE_ROOT_STR not in sys.path:
    sys.path.append(_SUBMODULE_ROOT_STR)


def format_comments_section(comments: list[dict]) -> str:
    """
    将评论列表格式化为 Markdown 评论章节文本。

    Args:
        comments: [{"author": str, "content": str, "likes": int}, ...]

    Returns:
        Markdown 格式的热门评论章节；如果 comments 为空返回空字符串
    """
    if not comments:
        return ""

    lines = ["\n\n---\n\n## 热门评论\n"]
    for i, c in enumerate(comments, 1):
        author = c.get("author", "匿名")
        content = c.get("content", "").strip().replace("\n", " ")
        likes = c.get("likes", 0)
        lines.append(f"{i}. 👤 {author}（👍 {likes}）：{content}")
    return "\n".join(lines)


async def fetch_bilibili_comments(aid: int, limit: int = 20) -> list[dict]:
    """
    获取 B站视频热门评论（无需登录，按热度排序）。

    Args:
        aid: 视频 av 号（整数）
        limit: 最多返回评论数，默认 20

    Returns:
        [{"author": str, "content": str, "likes": int}, ...]
        抓取失败时返回空列表
    """
    url = "https://api.bilibili.com/x/v2/reply"
    params = {
        "type": 1,   # 视频评论
        "oid": aid,
        "sort": 2,   # 按热度排序
        "ps": limit,
        "pn": 1,
    }
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Referer": "https://www.bilibili.com/",
    }
    try:
        async with httpx.AsyncClient(timeout=15.0, headers=headers) as client:
            resp = await client.get(url, params=params)
            data = resp.json()

        if data.get("code") != 0:
            logger.debug(
                f"[Comments] B站评论接口返回错误 aid={aid}: {data.get('message')}"
            )
            return []

        replies = (data.get("data") or {}).get("replies") or []
        result: list[dict] = []
        for r in replies[:limit]:
            author = (r.get("member") or {}).get("uname", "匿名")
            content = (r.get("content") or {}).get("message", "").strip()
            likes = r.get("like", 0)
            if content:
                result.append({"author": author, "content": content, "likes": likes})

        logger.info(f"[Comments] 获取 B站热门评论 aid={aid}: {len(result)} 条")
        return result

    except Exception as e:
        logger.warning(f"[Comments] 获取 B站评论失败 aid={aid}: {e}")
        return []


async def fetch_douyin_comments(
    aweme_id: str, cookie: str, limit: int = 20
) -> list[dict]:
    """
    获取抖音视频热门评论（需要有效 Cookie）。

    直接使用 httpx 请求，绕过 BaseCrawler，完整打印请求/响应用于调试。
    """
    if not cookie:
        logger.info(
            f"[Comments] 未配置抖音 Cookie，跳过评论抓取 aweme_id={aweme_id}"
        )
        return []

    logger.info(f"[Comments] 开始获取抖音热门评论 aweme_id={aweme_id}，最多 {limit} 条")

    try:
        import yaml  # noqa: PLC0415

        from crawlers.douyin.web.endpoints import DouyinAPIEndpoints  # noqa: PLC0415
        from crawlers.douyin.web.models import PostComments  # noqa: PLC0415
        from crawlers.douyin.web.utils import BogusManager  # noqa: PLC0415

        _config_path = (
            _SUBMODULE_ROOT / "crawlers" / "douyin" / "web" / "config.yaml"
        )
        with open(_config_path, "r", encoding="utf-8") as f:
            _cfg = yaml.safe_load(f)
        douyin_cfg = _cfg["TokenManager"]["douyin"]

        headers = {
            "Accept-Language": douyin_cfg["headers"]["Accept-Language"],
            "User-Agent": douyin_cfg["headers"]["User-Agent"],
            "Referer": douyin_cfg["headers"]["Referer"],
            "Cookie": cookie,
        }
        proxy_http = douyin_cfg["proxies"].get("http") or ""
        proxy_https = douyin_cfg["proxies"].get("https") or ""
        proxies = {}
        if proxy_http:
            proxies["http://"] = proxy_http
        if proxy_https:
            proxies["https://"] = proxy_https

        params = PostComments(aweme_id=aweme_id, cursor=0, count=limit)
        endpoint = BogusManager.xb_model_2_endpoint(
            DouyinAPIEndpoints.POST_COMMENT,
            params.dict(),
            headers["User-Agent"],
        )

        # Log full request details (mask most of the cookie for privacy)
        cookie_preview = cookie[:20] + "..." if len(cookie) > 20 else cookie
        logger.info(
            f"[Comments] 接口: POST_COMMENT = {DouyinAPIEndpoints.POST_COMMENT}\n"
            f"  Cookie（前20字符）: {cookie_preview}\n"
            f"  User-Agent: {headers['User-Agent'][:60]}\n"
            f"  完整请求 URL: {endpoint}"
        )

        async with httpx.AsyncClient(
            timeout=20.0,
            follow_redirects=True,
            headers=headers,
            proxies=proxies or None,
        ) as client:
            resp = await client.get(endpoint)

        logger.info(
            f"[Comments] 响应状态码: {resp.status_code}，"
            f"Content-Type: {resp.headers.get('content-type', '未知')}，"
            f"响应长度: {len(resp.content)} 字节"
        )
        logger.info(
            f"[Comments] 响应正文（前500字符）: {resp.text[:500]}"
        )

        if resp.status_code != 200:
            logger.warning(f"[Comments] HTTP 错误 status={resp.status_code} aweme_id={aweme_id}")
            return []

        if not resp.text.strip():
            logger.warning(f"[Comments] 响应正文为空 aweme_id={aweme_id}（Cookie 可能已失效或被风控）")
            return []

        try:
            data = resp.json()
        except Exception as json_err:
            logger.warning(f"[Comments] JSON 解析失败 aweme_id={aweme_id}: {json_err}，原始: {resp.text[:200]}")
            return []

        # status_code 0 or missing = success
        status = data.get("status_code")
        if status not in (0, None):
            logger.warning(
                f"[Comments] API 返回非零状态 status_code={status} aweme_id={aweme_id}，"
                f"响应 keys={list(data.keys())[:8]}"
            )
            return []

        comment_list = data.get("comments") or []
        logger.info(
            f"[Comments] API 成功 aweme_id={aweme_id}，返回 {len(comment_list)} 条评论"
        )
        result: list[dict] = []
        for c in comment_list[:limit]:
            user = c.get("user") or {}
            author = user.get("nickname", "匿名")
            content = (c.get("text") or "").strip()
            likes = c.get("digg_count", 0)
            if content:
                result.append({"author": author, "content": content, "likes": likes})

        logger.info(
            f"[Comments] 获取完成 aweme_id={aweme_id}: {len(result)} 条有效评论"
        )
        return result

    except Exception as e:
        logger.warning(
            f"[Comments] 获取抖音评论失败 aweme_id={aweme_id}: {type(e).__name__}: {e}"
        )
        return []
