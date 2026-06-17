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
import re
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
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
        self._favorites_url: Optional[str] = None  # 缓存有效的收藏夹端点

    # ==================== 设备 ID ====================

    @property
    def device_id(self) -> str:
        if not self._device_id:
            token = self.refresh_token or self.access_token or "default"
            normalized = re.sub(r"[^a-f0-9]", "", token.lower())
            if len(normalized) < 32:
                normalized = normalized.ljust(32, "0")
            normalized = normalized[:32]
            self._device_id = (
                f"{normalized[:8]}-{normalized[8:12]}-"
                f"4{normalized[13:16]}-a{normalized[17:20]}-{normalized[20:32]}"
            ).upper()
        return self._device_id

    def _build_headers(self, credential: Optional[dict] = None) -> dict:
        """App API 请求头（用于 api.xiaoyuzhoufm.com）"""
        cred = credential or {}
        access = cred.get("accessToken") or self.access_token or ""
        refresh = cred.get("refreshToken") or self.refresh_token or ""
        headers = {
            "Host": "api.xiaoyuzhoufm.com",
            "User-Agent": f"Xiaoyuzhou/{self.APP_VERSION} (build:{self.BUILD}; iOS 17.4.1)",
            "Market": "AppStore",
            "App-BuildNo": self.BUILD,
            "OS": "ios",
            "x-jike-device-id": self.device_id,
            "Manufacturer": "Apple",
            "BundleID": "app.podcast.cosmos",
            "Connection": "keep-alive",
            "Accept-Language": "zh-Hant-HK;q=1.0, zh-Hans-CN;q=0.9",
            "Model": "iPhone14,2",
            "app-permissions": "4",
            "Accept": "*/*",
            "App-Version": self.APP_VERSION,
            "WifiConnected": "true",
            "OS-Version": "17.4.1",
            "x-custom-xiaoyuzhou-app-dev": "",
            "abtest-info": "{}",
            "Timezone": "Asia/Shanghai",
            "Local-Time": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            "Content-Type": "application/json",
        }
        if access:
            headers["x-jike-access-token"] = access
        if refresh:
            headers["x-jike-refresh-token"] = refresh
        return headers

    def _creator_headers(self) -> dict:
        """Podcaster API 请求头（用于 podcaster-api.xiaoyuzhoufm.com 的登录接口）"""
        return {
            "accept": "application/json, text/plain, */*",
            "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
            "content-type": "application/json;charset=UTF-8",
            "x-app-build-time": "2026-04-08 10:43:10 +0800",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Origin": "https://podcaster.xiaoyuzhoufm.com",
            "Referer": "https://podcaster.xiaoyuzhoufm.com/login",
        }

    # ==================== 登录 ====================

    async def send_sms_code(self, phone: str) -> bool:
        """发送短信验证码"""
        url = f"{self.PODCASTER_BASE}/v1/auth/send-code"
        body = {"areaCode": "+86", "mobilePhoneNumber": phone}
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, json=body, headers=self._creator_headers())
        if resp.status_code == 200:
            logger.info(f"[Xiaoyuzhou] 验证码已发送至 {phone}")
            return True
        logger.warning(f"[Xiaoyuzhou] 发送验证码失败: {resp.status_code} {resp.text[:200]}")
        return False

    async def login_with_sms(self, phone: str, code: str) -> Optional[dict]:
        """
        短信验证码登录。

        登录后立即调用 /app_auth_tokens.refresh 换取稳定 App Token，
        这步是必须的——SMS token 仅用于换取 App token，不能直接访问 API。

        Returns:
            {"access_token": ..., "refresh_token": ..., "uid": ..., "nickname": ...}
            or None on failure
        """
        url = f"{self.PODCASTER_BASE}/v1/auth/login-with-sms"
        body = {"areaCode": "+86", "mobilePhoneNumber": phone, "verifyCode": code}
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, json=body, headers=self._creator_headers())

        if resp.status_code != 200:
            logger.warning(f"[Xiaoyuzhou] 登录失败: {resp.status_code} {resp.text[:300]}")
            return None

        # Token 在响应头中
        sms_access = resp.headers.get("x-jike-access-token", "")
        sms_refresh = resp.headers.get("x-jike-refresh-token", "")

        if not sms_access:
            logger.warning("[Xiaoyuzhou] 登录响应中未找到 token")
            return None

        # 解析用户信息
        uid = ""
        nickname = ""
        try:
            data = resp.json()
            user = data.get("data", {}).get("user") or data.get("userInfo") or {}
            uid = user.get("uid") or user.get("id") or ""
            nickname = user.get("nickname") or ""
        except Exception:
            pass

        # 立即用 SMS token 换取稳定 App Token
        self.access_token = sms_access
        self.refresh_token = sms_refresh
        self._device_id = None  # 重新基于 refresh token 生成

        stable = await self._do_refresh({"accessToken": sms_access, "refreshToken": sms_refresh})
        if stable:
            self.access_token = stable["accessToken"]
            self.refresh_token = stable["refreshToken"]
            self._device_id = None
            logger.info(f"[Xiaoyuzhou] 登录并换取 App Token 成功，uid={uid}, nickname={nickname}")
        else:
            logger.warning("[Xiaoyuzhou] 换取稳定 App Token 失败，使用原始 SMS Token")

        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "uid": uid,
            "nickname": nickname,
        }

    async def refresh_access_token(self) -> bool:
        """刷新 access token（使用 /app_auth_tokens.refresh 端点）"""
        if not self.refresh_token:
            return False
        cred = {"accessToken": self.access_token or "", "refreshToken": self.refresh_token}
        stable = await self._do_refresh(cred)
        if stable:
            self.access_token = stable["accessToken"]
            self.refresh_token = stable["refreshToken"]
            self._device_id = None
            logger.info("[Xiaoyuzhou] Token 刷新成功")
            return True
        return False

    async def _do_refresh(self, credential: dict) -> Optional[dict]:
        """内部：调用 /app_auth_tokens.refresh，返回新的 {accessToken, refreshToken} 或 None"""
        url = f"{self.API_BASE}/app_auth_tokens.refresh"
        headers = self._build_headers(credential)
        headers["Content-Type"] = "application/x-www-form-urlencoded; charset=utf-8"
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, data=None, headers=headers)

        new_access = resp.headers.get("x-jike-access-token", "")
        new_refresh = resp.headers.get("x-jike-refresh-token", "")
        if new_access and new_refresh:
            return {"accessToken": new_access, "refreshToken": new_refresh}

        logger.warning(f"[Xiaoyuzhou] Token 刷新失败: {resp.status_code} {resp.text[:200]}")
        return None

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
            "limit": limit,
            "sortOrder": "desc",
            "sortBy": "subscribedAt",
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

    async def get_inbox_list(
        self, limit: int = 50, load_more_key: Optional[dict] = None
    ) -> dict:
        """
        获取收件箱单集列表（所有订阅播客的最新单集合流，需要登录）

        Returns:
            {"episodes": [...], "load_more_key": {...} or None}
            每个 episode 附带 podcast_title 字段
        """
        if not self.access_token:
            raise RuntimeError("未登录小宇宙，请先调用 login_with_sms()")

        url = f"{self.API_BASE}/v1/inbox/list"
        body: dict = {"limit": limit}
        if load_more_key:
            body["loadMoreKey"] = load_more_key

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, json=body, headers=self._build_headers())

        if resp.status_code == 401:
            if await self.refresh_access_token():
                return await self.get_inbox_list(limit, load_more_key)
            raise RuntimeError("小宇宙 Token 已过期，请重新登录")

        if resp.status_code != 200:
            logger.warning(f"[Xiaoyuzhou] 获取收件箱失败: {resp.status_code} {resp.text[:300]}")
            return {"episodes": [], "load_more_key": None}

        try:
            data = resp.json()
        except Exception as e:
            logger.warning(f"[Xiaoyuzhou] 解析收件箱响应失败: {e}")
            return {"episodes": [], "load_more_key": None}

        raw_items = data.get("data") or data.get("list") or []
        episodes = []
        for item in raw_items:
            # inbox 条目包含 episode + podcast 两层
            raw_ep = item.get("episode") or item
            ep = self._normalize_episode(raw_ep)
            podcast = item.get("podcast") or {}
            ep["podcast_title"] = (
                podcast.get("title") or podcast.get("name") or ""
            )
            episodes.append(ep)
            if limit > 0 and len(episodes) >= limit:
                break

        next_key = data.get("loadMoreKey") or data.get("nextKey")
        logger.info(f"[Xiaoyuzhou] 收件箱获取成功，共 {len(episodes)} 集")
        return {"episodes": episodes, "load_more_key": next_key}

    async def get_favorites(
        self, limit: int = 50, load_more_key: Optional[dict] = None
    ) -> dict:
        """
        获取用户收藏的单集列表（需要登录）

        Returns:
            {"episodes": [...], "load_more_key": {...} or None}
        """
        if not self.access_token:
            raise RuntimeError("未登录小宇宙，请先调用 login_with_sms()")

        # 优先使用已探测到的有效端点，避免每次分页重试
        candidates = (
            [self._favorites_url] if self._favorites_url else [
                f"{self.API_BASE}/v1/starred-episode/list",
                f"{self.API_BASE}/v1/favorite/list",
                f"{self.API_BASE}/v1/collect/list",
            ]
        )
        body: dict = {"limit": limit}
        if load_more_key:
            body["loadMoreKey"] = load_more_key

        async with httpx.AsyncClient(timeout=30) as client:
            for url in candidates:
                resp = await client.post(url, json=body, headers=self._build_headers())
                if resp.status_code == 401:
                    if await self.refresh_access_token():
                        return await self.get_favorites(limit, load_more_key)
                    raise RuntimeError("小宇宙 Token 已过期，请重新登录")
                if resp.status_code == 200:
                    if self._favorites_url != url:
                        self._favorites_url = url
                        logger.info(f"[Xiaoyuzhou] 收藏夹端点: {url}")
                    break
                logger.debug(f"[Xiaoyuzhou] 收藏夹端点 {url} 返回 {resp.status_code}，尝试下一个")
            else:
                logger.warning(f"[Xiaoyuzhou] 所有收藏夹端点均失败，最后状态: {resp.status_code} {resp.text[:200]}")
                return {"episodes": [], "load_more_key": None}

        try:
            data = resp.json()
        except Exception as e:
            logger.warning(f"[Xiaoyuzhou] 解析收藏夹响应失败: {e}")
            return {"episodes": [], "load_more_key": None}

        raw_items = data.get("data") or data.get("list") or data.get("episodes") or []
        episodes = []
        for item in raw_items:
            raw_ep = item.get("episode") or item
            ep = self._normalize_episode(raw_ep)
            podcast = item.get("podcast") or {}
            ep["podcast_title"] = podcast.get("title") or podcast.get("name") or ""
            episodes.append(ep)
            if limit > 0 and len(episodes) >= limit:
                break

        next_key = data.get("loadMoreKey") or data.get("nextKey")
        logger.info(f"[Xiaoyuzhou] 收藏夹获取成功，共 {len(episodes)} 集")
        return {"episodes": episodes, "load_more_key": next_key}

    async def get_transcript(self, episode_id: str, media_id: str = "") -> Optional[str]:
        """
        获取单集字幕/文字稿（需要登录）
        有字幕时直接返回文本，无需 ASR 转写。

        Returns:
            字幕文本（纯文字），或 None（无字幕/失败）
        """
        if not self.access_token:
            return None

        url = f"{self.API_BASE}/v1/episode-transcript/get"
        body: dict = {"eid": episode_id}
        if media_id:
            body["mediaId"] = media_id

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(url, json=body, headers=self._build_headers())

            if resp.status_code == 401:
                if await self.refresh_access_token():
                    return await self.get_transcript(episode_id, media_id)
                return None

            if resp.status_code != 200:
                logger.debug(f"[Xiaoyuzhou] 获取字幕失败: {resp.status_code} eid={episode_id}")
                return None

            data = resp.json()
            transcript_url = (data.get("data") or {}).get("transcriptUrl") or data.get("transcriptUrl")
            if not transcript_url:
                return None

            # 下载字幕内容
            async with httpx.AsyncClient(timeout=60) as client:
                tr = await client.get(transcript_url)
            tr.raise_for_status()
            # 字幕通常是 JSON 格式的时间轴，提取纯文字
            return self._parse_transcript_content(tr.text)

        except Exception as e:
            logger.debug(f"[Xiaoyuzhou] 获取字幕异常: {e} eid={episode_id}")
            return None

    @staticmethod
    def _ms_to_timestamp(ms: int) -> str:
        s = ms // 1000
        h, rem = divmod(s, 3600)
        m, sec = divmod(rem, 60)
        return f"{h:02d}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"

    @staticmethod
    def _detect_speaker(text: str, known_speakers: dict, current_label: str) -> str:
        """
        检测说话人：
        1. 文本中含有自我介绍关键词 → 提取姓名/称呼
        2. 否则沿用当前说话人
        """
        patterns = [
            r'我是([^\s，。！？、,\.]{2,6})(?:[，,。！？\s]|$)',
            r'大家好[，,]?我是([^\s，。！？、,\.]{2,6})',
            r'Hello[，,]?\s*(?:大家好[，,]?\s*)?我是([^\s，。！？、,\.]{2,6})',
        ]
        for pat in patterns:
            m = re.search(pat, text)
            if m:
                name = m.group(1).strip()
                # 记录已知说话人
                if name not in known_speakers.values():
                    idx = len(known_speakers) + 1
                    known_speakers[f"S{idx}"] = name
                return name
        return current_label

    @staticmethod
    def _extract_key_dialogues(segments: list[dict], max_items: int = 8) -> list[str]:
        """
        从分段中提取关键对话：
        - 含问句的段落（疑问词 / 问号）
        - 含观点性词汇的段落（我认为/其实/关键/重要/核心/本质）
        """
        key_patterns = re.compile(
            r'[？?]|为什么|怎么|如何|是否|难道|其实|关键|核心|本质|重要|问题是|我认为|我觉得|值得注意'
        )
        results = []
        for seg in segments:
            text = seg.get("text", "")
            if key_patterns.search(text) and len(text) >= 20:
                ts = seg.get("timestamp", "")
                label = seg.get("speaker", "")
                prefix = f"[{ts}] **{label}**: " if ts and label else ""
                # 截取前120字
                snippet = text[:120] + ("…" if len(text) > 120 else "")
                results.append(prefix + snippet)
            if len(results) >= max_items:
                break
        return results

    @staticmethod
    def _parse_transcript_content(raw: str) -> Optional[str]:
        """解析字幕 JSON，返回带说话人标注和时间戳的 Markdown 格式文本"""
        import json as _json
        import re as _re

        try:
            data = _json.loads(raw)
        except Exception:
            # 非 JSON 直接返回原文
            return raw.strip() if raw and len(raw) > 10 else None

        # 提取 utterances/sentences 列表，兼容多种字段名
        utterances = (
            data.get("utterances")
            or data.get("sentences")
            or data.get("segments")
            or []
        )
        if not utterances:
            text = data.get("text") or data.get("content") or ""
            return text.strip() if text else None

        # ── 按说话人+长度分段 ──────────────────────────────────────────
        MAX_CHARS = 300
        known_speakers: dict = {}
        current_label = "说话人"
        current_speaker_id = None
        segment_text = ""
        segment_start = 0
        char_count = 0
        output_segments: list[dict] = []

        def flush_segment():
            if segment_text.strip():
                ts = XiaoyuzhouService._ms_to_timestamp(segment_start)
                output_segments.append({
                    "speaker": current_label,
                    "timestamp": ts,
                    "text": segment_text.strip(),
                })

        for utt in utterances:
            text = utt.get("text") or utt.get("content") or ""
            if not text:
                continue
            start_ms = (
                utt.get("startTime")
                or utt.get("start_time")
                or utt.get("begin_time")
                or utt.get("start")
                or 0
            )
            spk_id = utt.get("speaker_id") or utt.get("speaker") or utt.get("spk")

            # 说话人切换检测
            if spk_id is not None and spk_id != current_speaker_id:
                flush_segment()
                current_speaker_id = spk_id
                # 分配标签（S1/S2/...）
                if spk_id not in known_speakers:
                    idx = len(known_speakers) + 1
                    known_speakers[spk_id] = f"说话人{chr(64+idx)}"  # A/B/C...
                current_label = known_speakers[spk_id]
                segment_text = ""
                segment_start = start_ms
                char_count = 0
            else:
                # 无 speaker_id 时用文本自我介绍识别
                detected = XiaoyuzhouService._detect_speaker(text, {}, current_label)
                if detected != current_label:
                    flush_segment()
                    current_label = detected
                    segment_text = ""
                    segment_start = start_ms
                    char_count = 0

            segment_text += text
            char_count += len(text)

            # 超过 MAX_CHARS 强制换段
            if char_count >= MAX_CHARS:
                flush_segment()
                segment_text = ""
                segment_start = start_ms
                char_count = 0

        flush_segment()

        if not output_segments:
            return None

        # ── 生成 Markdown ───────────────────────────────────────────────
        md_lines: list[str] = []
        for seg in output_segments:
            md_lines.append(f"\n**{seg['speaker']}** [{seg['timestamp']}]\n")
            md_lines.append(seg["text"] + "\n")

        # 关键对话区块
        key_items = XiaoyuzhouService._extract_key_dialogues(output_segments)
        if key_items:
            md_lines.append("\n---\n\n### 🔑 关键对话\n")
            for item in key_items:
                md_lines.append(f"\n> {item}\n")

        return "".join(md_lines).strip()


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
        body: dict = {"pid": podcast_id, "order": "desc", "limit": limit}
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
