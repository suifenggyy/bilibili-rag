"""
评论抓取与格式化工具

支持 B站 和 抖音两个平台的热门评论获取，抓取失败时静默跳过（非阻断性）。
"""
from typing import Optional

import httpx
from loguru import logger

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

    使用 v5 桥接层，与收藏夹共享签名和浏览器传输逻辑。
    """
    if not cookie:
        logger.info(
            f"[Comments] 未配置抖音 Cookie，跳过评论抓取 aweme_id={aweme_id}"
        )
        return []

    logger.info(f"[Comments] 开始获取抖音热门评论 aweme_id={aweme_id}，最多 {limit} 条")

    try:
        from app.services import douyin_bridge

        data = await douyin_bridge.fetch_comments(cookie, aweme_id, count=limit)

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
