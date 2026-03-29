"""
抖音收藏夹服务

通过 Evil0ctal/Douyin_TikTok_Download_API 中间层获取抖音收藏夹视频列表。
该中间层已处理 A-Bogus/X-Bogus 动态签名，无需自行逆向。

部署 Evil0ctal API 服务：
    git clone https://github.com/Evil0ctal/Douyin_TikTok_Download_API
    cd Douyin_TikTok_Download_API
    pip install -r requirements.txt
    python main.py  # 默认运行在 http://localhost:2333

Cookie 获取方式（浏览器手动复制）：
    1. 用 Chrome/Edge 打开 https://www.douyin.com 并登录
    2. 按 F12 打开开发者工具 → Application → Cookies → douyin.com
    3. 复制所有 Cookie 字段，拼接为 "key1=val1; key2=val2; ..." 格式
    4. 配置到 DOUYIN_COOKIE 环境变量，或通过 --cookie 参数传入
"""

import asyncio
from typing import Optional

import httpx
from loguru import logger


class DouyinService:
    """
    抖音数据服务，依赖本地运行的 Evil0ctal API 中间层。

    Evil0ctal 已实现 A-Bogus/X-Bogus 动态签名，本服务直接调用其 HTTP 接口。
    Cookie 需用户从浏览器手动获取（抖音不提供扫码登录 API）。
    """

    def __init__(
        self,
        cookie: str,
        evil0ctal_url: str = "http://localhost:2333",
        timeout: float = 30.0,
    ):
        """
        Args:
            cookie:         浏览器 Cookie 字符串（"key1=val1; key2=val2; ..."）
            evil0ctal_url:  Evil0ctal API 服务地址
            timeout:        HTTP 请求超时秒数
        """
        self.cookie = cookie
        self.evil0ctal_url = evil0ctal_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=timeout)

    async def close(self) -> None:
        await self._client.aclose()

    # ==================== 可用性检查 ====================

    async def check_evil0ctal_available(self) -> bool:
        """检查 Evil0ctal API 服务是否可达"""
        try:
            resp = await self._client.get(
                f"{self.evil0ctal_url}/docs",
                timeout=5.0,
                follow_redirects=True,
            )
            return resp.status_code < 500
        except Exception:
            return False

    async def check_cookie_valid(self) -> bool:
        """检查 Cookie 是否有效（通过拉取一页收藏夹来验证）"""
        try:
            data = await self.get_collection_videos_page(count=1)
            # 如果未返回错误且有数据结构，则认为有效
            return isinstance(data.get("aweme_list"), list)
        except Exception as e:
            logger.debug(f"[Douyin] Cookie 校验失败: {e}")
            return False

    # ==================== 收藏夹接口 ====================

    async def get_collection_videos_page(
        self, max_cursor: int = 0, count: int = 20
    ) -> dict:
        """
        获取一页收藏夹视频。

        Returns:
            dict，包含以下字段：
                aweme_list  (list)  本页视频列表
                has_more    (int)   是否有更多（1=有，0=无）
                max_cursor  (int)   下一页游标
        """
        resp = await self._client.get(
            f"{self.evil0ctal_url}/api/douyin/web/fetch_user_collection_videos",
            params={
                "cookie": self.cookie,
                "max_cursor": max_cursor,
                "count": count,
            },
        )
        resp.raise_for_status()
        result = resp.json()

        # Evil0ctal 响应格式：{"code": 200, "data": {...}}
        if result.get("code") not in (200, 0, None):
            msg = result.get("message") or result.get("msg") or "未知错误"
            raise RuntimeError(f"Evil0ctal API 返回错误: code={result.get('code')}, {msg}")

        # 兼容两种响应层级
        inner = result.get("data") or result
        return {
            "aweme_list": inner.get("aweme_list") or [],
            "has_more": int(inner.get("has_more") or 0),
            "max_cursor": int(inner.get("max_cursor") or 0),
        }

    async def get_all_collection_videos(self, max_pages: int = 200) -> list[dict]:
        """
        获取全部收藏夹视频（自动翻页）。

        Args:
            max_pages: 最大翻页次数，防止无限循环（默认 200 页 × 20 条 = 4000 个）

        Returns:
            视频数据列表，每条为 dict（Evil0ctal 原始返回格式）
        """
        all_videos: list[dict] = []
        max_cursor = 0
        page = 0

        while page < max_pages:
            page += 1
            logger.debug(f"[Douyin] 获取收藏夹第 {page} 页 (max_cursor={max_cursor})")

            try:
                data = await self.get_collection_videos_page(max_cursor=max_cursor)
            except Exception as e:
                logger.error(f"[Douyin] 获取第 {page} 页失败: {e}")
                break

            videos = data.get("aweme_list", [])
            if not videos:
                break

            all_videos.extend(videos)
            logger.debug(f"[Douyin] 第 {page} 页获取 {len(videos)} 个视频，累计 {len(all_videos)} 个")

            if not data.get("has_more"):
                break

            max_cursor = data.get("max_cursor", 0)
            if not max_cursor:
                break

            # 礼貌性限速
            await asyncio.sleep(0.8)

        return all_videos

    # ==================== 视频信息解析 ====================

    @staticmethod
    def parse_video_info(raw: dict) -> dict:
        """
        从 Evil0ctal 原始响应中提取常用字段，返回统一结构的 dict。

        返回字段：
            aweme_id    str   视频 ID
            title       str   视频描述/标题
            author      str   作者昵称
            author_uid  str   作者 UID
            create_time int   发布时间戳
            duration    int   视频时长（毫秒）
            cover_url   str   封面图 URL
            play_urls   list  播放 URL 列表（无水印优先）
            share_url   str   分享 URL
        """
        aweme_id = raw.get("aweme_id") or raw.get("id") or ""
        title = (raw.get("desc") or "").strip() or aweme_id

        author = raw.get("author") or {}
        author_name = author.get("nickname") or "未知作者"
        author_uid = author.get("uid") or author.get("sec_uid") or ""

        create_time = raw.get("create_time") or 0

        video = raw.get("video") or {}
        duration = video.get("duration") or 0  # 毫秒

        # 封面：优先 cover，其次 cover_medium
        cover_urls = (
            (video.get("cover") or {}).get("url_list")
            or (video.get("cover_medium") or {}).get("url_list")
            or []
        )
        cover_url = cover_urls[0] if cover_urls else ""

        # 播放 URL：优先 play_addr（无水印），其次 download_addr
        play_urls = []
        for key in ("play_addr", "play_addr_h264", "play_addr_bytevc1", "download_addr"):
            addr = video.get(key) or {}
            urls = addr.get("url_list") or []
            if urls:
                play_urls = urls
                break

        # 分享链接
        share_url = f"https://www.douyin.com/video/{aweme_id}" if aweme_id else ""

        return {
            "aweme_id": aweme_id,
            "title": title,
            "author": author_name,
            "author_uid": author_uid,
            "create_time": create_time,
            "duration": duration,
            "cover_url": cover_url,
            "play_urls": play_urls,
            "share_url": share_url,
        }
