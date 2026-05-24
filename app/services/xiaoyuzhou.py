"""
小宇宙播客服务模块

支持：
- 手机号 + 短信验证码登录（获取 access/refresh token）
- 获取用户订阅的播客列表
- 获取播客单集列表
- 通过 RSS 解析播客/集数信息（无需登录）
- Token 自动刷新
"""
import hashlib
import uuid
import xml.etree.ElementTree as ET
from typing import Optional

import httpx
from loguru import logger


class XiaoyuzhouService:
    """小宇宙播客服务"""

    API_BASE = "https://api.xiaoyuzhoufm.com"
    PODCASTER_BASE = "https://podcaster-api.xiaoyuzhoufm.com"
    APP_VERSION = "2.57.1"
    BUILD = "1576"

    def __init__(
        self,
        access_token: Optional[str] = None,
        refresh_token: Optional[str] = None,
    ):
        self.access_token = access_token
        self.refresh_token = refresh_token
        self._device_id: Optional[str] = None

    # ==================== 设备 ID ====================

    @property
    def device_id(self) -> str:
        if not self._device_id:
            seed = (self.access_token or self.refresh_token or "default")
            self._device_id = str(
                uuid.UUID(hashlib.md5(seed.encode()).hexdigest())
            )
        return self._device_id

    def _build_headers(self) -> dict:
        headers = {
            "User-Agent": f"Xiaoyuzhou/{self.APP_VERSION} (build:{self.BUILD}; iOS 17.4.1)",
            "BundleID": "app.podcast.cosmos",
            "x-jike-device-id": self.device_id,
            "Content-Type": "application/json",
        }
        if self.access_token:
            headers["x-jike-access-token"] = self.access_token
        return headers

    # ==================== 登录 ====================

    async def send_sms_code(self, phone: str) -> bool:
        """发送短信验证码"""
        url = f"{self.PODCASTER_BASE}/v1/auth/send-sms"
        body = {"areaCode": "+86", "mobilePhoneNumber": phone}
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, json=body, headers=self._build_headers())
        if resp.status_code == 200:
            logger.info(f"[Xiaoyuzhou] 验证码已发送至 {phone}")
            return True
        logger.warning(f"[Xiaoyuzhou] 发送验证码失败: {resp.status_code} {resp.text[:200]}")
        return False

    async def login_with_sms(self, phone: str, code: str) -> Optional[dict]:
        """
        短信验证码登录

        Returns:
            {"access_token": ..., "refresh_token": ..., "uid": ..., "nickname": ...}
            or None on failure
        """
        url = f"{self.PODCASTER_BASE}/v1/auth/login-with-sms"
        body = {"areaCode": "+86", "mobilePhoneNumber": phone, "verifyCode": code}
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, json=body, headers=self._build_headers())

        if resp.status_code != 200:
            logger.warning(f"[Xiaoyuzhou] 登录失败: {resp.status_code} {resp.text[:300]}")
            return None

        # Token 在响应头中
        access_token = resp.headers.get("x-jike-access-token", "")
        refresh_token = resp.headers.get("x-jike-refresh-token", "")

        if not access_token:
            # 兜底：某些版本 token 在 body 中
            try:
                data = resp.json()
                cred = data.get("credential") or data.get("data") or {}
                access_token = cred.get("accessToken") or cred.get("access_token") or ""
                refresh_token = cred.get("refreshToken") or cred.get("refresh_token") or ""
            except Exception:
                pass

        if not access_token:
            logger.warning("[Xiaoyuzhou] 登录响应中未找到 token")
            return None

        self.access_token = access_token
        self.refresh_token = refresh_token
        self._device_id = None  # 重置设备 ID（会重新基于 token 生成）

        # 解析用户信息（body 中）
        uid = ""
        nickname = ""
        try:
            data = resp.json()
            user_info = data.get("userInfo") or {}
            uid = user_info.get("uid") or user_info.get("id") or ""
            nickname = user_info.get("nickname") or ""
        except Exception:
            pass

        logger.info(f"[Xiaoyuzhou] 登录成功，uid={uid}, nickname={nickname}")
        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "uid": uid,
            "nickname": nickname,
        }

    async def refresh_access_token(self) -> bool:
        """刷新 access token"""
        if not self.refresh_token:
            return False
        url = f"{self.API_BASE}/v1/auth/refresh-token"
        headers = self._build_headers()
        headers["x-jike-refresh-token"] = self.refresh_token
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, json={}, headers=headers)

        new_access = resp.headers.get("x-jike-access-token", "")
        new_refresh = resp.headers.get("x-jike-refresh-token", "")
        if new_access:
            self.access_token = new_access
            if new_refresh:
                self.refresh_token = new_refresh
            self._device_id = None
            logger.info("[Xiaoyuzhou] Token 刷新成功")
            return True

        logger.warning(f"[Xiaoyuzhou] Token 刷新失败: {resp.status_code} {resp.text[:200]}")
        return False

    # ==================== 订阅列表 ====================

    async def get_subscriptions(self, limit: int = 50) -> list[dict]:
        """
        获取用户订阅的播客列表（需要登录）

        Returns:
            list of podcast dicts with keys: podcast_id, title, author, rss_url, cover_url, description
        """
        if not self.access_token:
            raise RuntimeError("未登录小宇宙，请先调用 login_with_sms()")

        url = f"{self.API_BASE}/v1/subscription/list"
        body = {
            "limit": str(limit),
            "sortOrder": "desc",
            "sortBy": "subscribedAt",
            "loadMoreKey": {},
        }
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, json=body, headers=self._build_headers())

        if resp.status_code == 401:
            logger.info("[Xiaoyuzhou] Token 过期，尝试刷新")
            if await self.refresh_access_token():
                return await self.get_subscriptions(limit)
            raise RuntimeError("小宇宙 Token 已过期，请重新登录")

        if resp.status_code != 200:
            logger.warning(f"[Xiaoyuzhou] 获取订阅失败: {resp.status_code} {resp.text[:300]}")
            return []

        try:
            data = resp.json()
        except Exception as e:
            logger.warning(f"[Xiaoyuzhou] 解析订阅响应失败: {e}")
            return []

        items = data.get("data") or data.get("subscriptions") or data.get("list") or []
        results = []
        for item in items:
            podcast = item.get("podcast") or item
            pid = podcast.get("pid") or podcast.get("id") or ""
            if not pid:
                continue
            results.append({
                "podcast_id": pid,
                "title": podcast.get("title") or podcast.get("name") or pid,
                "author": podcast.get("author") or podcast.get("uploader") or "",
                "description": (podcast.get("brief") or podcast.get("description") or "")[:500],
                "cover_url": podcast.get("image") or podcast.get("cover") or "",
                "rss_url": podcast.get("rss") or f"https://feeds.xiaoyuzhoufm.com/podcast/{pid}",
            })

        logger.info(f"[Xiaoyuzhou] 获取订阅列表成功，共 {len(results)} 个播客")
        return results

    # ==================== 单集列表（API）====================

    async def get_episodes_by_api(
        self, podcast_id: str, limit: int = 20, load_more_key: Optional[dict] = None
    ) -> dict:
        """
        通过 API 获取播客单集列表（需要登录）

        Returns:
            {"episodes": [...], "load_more_key": {...} or None}
        """
        if not self.access_token:
            raise RuntimeError("未登录小宇宙，请先调用 login_with_sms()")

        url = f"{self.API_BASE}/v1/episode/list"
        body: dict = {"pid": podcast_id, "order": "desc"}
        if load_more_key:
            body["loadMoreKey"] = load_more_key

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, json=body, headers=self._build_headers())

        if resp.status_code == 401:
            if await self.refresh_access_token():
                return await self.get_episodes_by_api(podcast_id, limit, load_more_key)
            raise RuntimeError("小宇宙 Token 已过期，请重新登录")

        if resp.status_code != 200:
            logger.warning(f"[Xiaoyuzhou] 获取单集列表失败: {resp.status_code} {resp.text[:300]}")
            return {"episodes": [], "load_more_key": None}

        try:
            data = resp.json()
        except Exception as e:
            logger.warning(f"[Xiaoyuzhou] 解析单集列表响应失败: {e}")
            return {"episodes": [], "load_more_key": None}

        raw_episodes = data.get("data") or data.get("list") or []
        episodes = [self._normalize_episode(ep) for ep in raw_episodes if ep]
        next_key = data.get("loadMoreKey") or data.get("nextKey")
        return {"episodes": episodes, "load_more_key": next_key}

    # ==================== RSS 解析（无需登录）====================

    async def get_podcast_info_from_rss(self, rss_url: str) -> Optional[dict]:
        """从 RSS Feed 获取播客元数据"""
        try:
            content = await self._fetch_rss(rss_url)
            return self._parse_podcast_info(content, rss_url)
        except Exception as e:
            logger.warning(f"[Xiaoyuzhou] 解析播客 RSS 失败 [{rss_url}]: {e}")
            return None

    async def get_episodes_from_rss(
        self, rss_url: str, limit: int = 0
    ) -> list[dict]:
        """
        从 RSS Feed 获取单集列表（无需登录）

        Args:
            rss_url: RSS 订阅地址
            limit: 最多返回条数，0 表示全部
        """
        try:
            content = await self._fetch_rss(rss_url)
            episodes = self._parse_episodes(content)
            if limit > 0:
                episodes = episodes[:limit]
            logger.info(f"[Xiaoyuzhou] RSS 解析成功：{rss_url}，共 {len(episodes)} 集")
            return episodes
        except Exception as e:
            logger.warning(f"[Xiaoyuzhou] 解析 RSS 失败 [{rss_url}]: {e}")
            return []

    def build_rss_url(self, podcast_id: str) -> str:
        return f"https://feeds.xiaoyuzhoufm.com/podcast/{podcast_id}"

    # ==================== 内部 RSS 工具 ====================

    @staticmethod
    async def _fetch_rss(rss_url: str) -> str:
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; PodcastBot/1.0)",
            "Accept": "application/rss+xml, application/xml, text/xml, */*",
        }
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(rss_url, headers=headers)
        resp.raise_for_status()
        return resp.text

    @staticmethod
    def _parse_podcast_info(content: str, rss_url: str) -> dict:
        root = ET.fromstring(content)
        channel = root.find("channel")
        if channel is None:
            return {}
        ns = {"itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd"}
        title = channel.findtext("title") or ""
        author = (
            channel.findtext("itunes:author", namespaces=ns)
            or channel.findtext("managingEditor")
            or ""
        )
        description = channel.findtext("description") or channel.findtext(
            "itunes:summary", namespaces=ns
        ) or ""
        cover_url = ""
        image_tag = channel.find("image")
        if image_tag is not None:
            cover_url = image_tag.findtext("url") or ""
        if not cover_url:
            itunes_image = channel.find("itunes:image", ns)
            if itunes_image is not None:
                cover_url = itunes_image.get("href") or ""
        # podcast_id 从 URL 中提取
        podcast_id = rss_url.rstrip("/").split("/")[-1]
        return {
            "podcast_id": podcast_id,
            "title": title,
            "author": author,
            "description": description[:500],
            "cover_url": cover_url,
            "rss_url": rss_url,
        }

    @staticmethod
    def _parse_episodes(content: str) -> list[dict]:
        root = ET.fromstring(content)
        channel = root.find("channel")
        if channel is None:
            return []
        ns = {"itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd"}
        episodes = []
        for item in channel.findall("item"):
            enclosure = item.find("enclosure")
            audio_url = ""
            if enclosure is not None:
                audio_url = enclosure.get("url") or ""
            episode_id = item.findtext("guid") or audio_url
            duration_text = item.findtext("itunes:duration", namespaces=ns) or ""
            duration_sec = _parse_duration(duration_text)
            episodes.append({
                "episode_id": episode_id,
                "title": item.findtext("title") or "",
                "description": (item.findtext("description") or "")[:500],
                "pub_date": item.findtext("pubDate") or "",
                "duration": duration_sec,
                "audio_url": audio_url,
                "cover_url": "",  # RSS 集数一般无独立封面
            })
        return episodes

    @staticmethod
    def _normalize_episode(raw: dict) -> dict:
        """规范化 API 返回的单集数据"""
        eid = raw.get("eid") or raw.get("id") or ""
        return {
            "episode_id": eid,
            "title": raw.get("title") or eid,
            "description": (raw.get("shownotes") or raw.get("description") or "")[:500],
            "pub_date": raw.get("pubDate") or "",
            "duration": raw.get("duration") or 0,
            "audio_url": raw.get("enclosure", {}).get("url") or raw.get("audio_url") or "",
            "cover_url": raw.get("image") or raw.get("cover_url") or "",
        }


def _parse_duration(text: str) -> int:
    """解析 RSS duration 字段（HH:MM:SS 或 MM:SS 或纯秒数）"""
    if not text:
        return 0
    text = text.strip()
    if text.isdigit():
        return int(text)
    parts = text.split(":")
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
    except ValueError:
        pass
    return 0
