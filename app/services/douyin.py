"""
抖音收藏夹服务

直接调用 douyin_tiktok_download_api 子模块（Evil0ctal/Douyin_TikTok_Download_API）
中的 Python 函数，无需启动外部 HTTP 服务。

子模块已处理 A-Bogus/X-Bogus 动态签名，支持通过 git submodule update 随时更新。

Cookie 获取方式（浏览器手动复制）：
    1. 用 Chrome/Edge 打开 https://www.douyin.com 并登录
    2. 按 F12 打开开发者工具 → Application → Cookies → douyin.com
    3. 复制所有 Cookie 字段，拼接为 "key1=val1; key2=val2; ..." 格式
    4. 配置到 DOUYIN_COOKIE 环境变量，或通过 --cookie 参数传入
"""

import asyncio
from typing import Optional

from loguru import logger

from app.services import douyin_bridge


class DouyinService:
    """
    抖音数据服务，通过子模块直接调用 Evil0ctal 爬虫函数。

    evil0ctal_url 参数已废弃（保留仅为 API 兼容），不再需要外部服务。
    """

    def __init__(
        self,
        cookie: str,
        evil0ctal_url: str = "",
        timeout: float = 30.0,
    ):
        """
        Args:
            cookie:         浏览器 Cookie 字符串（"key1=val1; key2=val2; ..."）
            evil0ctal_url:  已废弃，保留仅为 API 向后兼容，不再使用
            timeout:        已废弃，保留仅为 API 向后兼容，不再使用
        """
        self.cookie = cookie
        if evil0ctal_url and evil0ctal_url not in ("", "http://localhost:2333"):
            logger.warning(
                "[Douyin] evil0ctal_url 参数已废弃（子模块直接调用模式），忽略: %s", evil0ctal_url
            )

    async def close(self) -> None:
        pass

    # ==================== 可用性检查 ====================

    async def check_evil0ctal_available(self) -> bool:
        """检查子模块是否可用（已安装依赖、子模块已初始化）。"""
        return douyin_bridge.is_available()

    async def check_cookie_valid(self) -> bool:
        """检查 Cookie 是否有效（通过拉取一页收藏夹来验证）"""
        try:
            data = await self.get_collection_videos_page(count=1)
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
        result = await douyin_bridge.fetch_collection_videos(
            cookie=self.cookie, cursor=max_cursor, count=count
        )
        return {
            "aweme_list": result.get("aweme_list") or [],
            "has_more": int(result.get("has_more") or 0),
            "max_cursor": int(result.get("max_cursor") or 0),
        }

    async def get_all_collection_videos(self, max_pages: int = 200) -> list[dict]:
        """
        获取全部收藏夹视频（自动翻页）。

        Args:
            max_pages: 最大翻页次数，防止无限循环（默认 200 页 × 20 条 = 4000 个）

        Returns:
            视频数据列表，每条为 dict（原始 Douyin API 格式）
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

            await asyncio.sleep(0.8)

        return all_videos

    # ==================== 创作者作品接口 ====================

    async def get_creator_videos_page(
        self, sec_uid: str, max_cursor: int = 0, count: int = 20
    ) -> dict:
        """
        获取一页创作者投稿视频。

        Args:
            sec_uid: 抖音用户 sec_uid 或主页 URL

        Returns:
            dict 包含：aweme_list, has_more, max_cursor
        """
        result = await douyin_bridge.fetch_user_post_videos(
            sec_user_id=sec_uid, max_cursor=max_cursor, count=count
        )
        return {
            "aweme_list": result.get("aweme_list") or [],
            "has_more": int(result.get("has_more") or 0),
            "max_cursor": int(result.get("max_cursor") or 0),
        }

    async def get_all_creator_videos(
        self,
        sec_uid: str,
        after_date: Optional[str] = None,
        max_pages: int = 200,
    ) -> list[dict]:
        """
        获取创作者全部投稿视频，可按发布日期过滤。

        Args:
            sec_uid: 抖音用户 sec_uid 或主页 URL
            after_date: 'YYYY-MM-DD' 格式，仅返回此日期之后发布的视频（含当天）
            max_pages: 最多翻页次数

        Returns:
            视频数据列表（parse_video_info 格式）
        """
        after_ts: Optional[int] = None
        if after_date:
            try:
                from datetime import datetime as _dt
                after_ts = int(_dt.strptime(after_date, "%Y-%m-%d").timestamp())
            except ValueError:
                logger.warning(f"[Douyin] 无效的 after_date: {after_date}，将忽略日期过滤")

        all_videos: list[dict] = []
        max_cursor = 0
        page = 0

        while page < max_pages:
            page += 1
            logger.debug(f"[Douyin] 获取创作者 {sec_uid[:20]}... 第 {page} 页")

            try:
                data = await self.get_creator_videos_page(sec_uid, max_cursor=max_cursor)
            except Exception as e:
                logger.error(f"[Douyin] 获取创作者视频第 {page} 页失败: {e}")
                break

            videos = data.get("aweme_list", [])
            if not videos:
                break

            for raw in videos:
                create_time = raw.get("create_time") or 0
                if after_ts and create_time < after_ts:
                    logger.debug(f"[Douyin] create_time={create_time} < after_ts={after_ts}，停止翻页")
                    return all_videos
                all_videos.append(raw)

            logger.debug(f"[Douyin] 第 {page} 页获取 {len(videos)} 个，累计 {len(all_videos)} 个")

            if not data.get("has_more"):
                break

            max_cursor = data.get("max_cursor", 0)
            if not max_cursor:
                break

            await asyncio.sleep(0.8)

        return all_videos

    # ==================== 视频信息解析 ====================

    @staticmethod
    def parse_video_info(raw: dict) -> dict:
        """
        从原始 Douyin API 响应中提取常用字段，返回统一结构的 dict。

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

        cover_urls = (
            (video.get("cover") or {}).get("url_list")
            or (video.get("cover_medium") or {}).get("url_list")
            or []
        )
        cover_url = cover_urls[0] if cover_urls else ""

        play_urls = []
        for key in ("play_addr", "play_addr_h264", "play_addr_bytevc1", "download_addr"):
            addr = video.get(key) or {}
            urls = addr.get("url_list") or []
            if urls:
                play_urls = urls
                break

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

