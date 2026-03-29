"""
Instapaper 书签服务

通过 Instapaper Full API（OAuth 1.0a xAuth）获取收藏书签列表。
免费账户也可使用，正文提取由 ArticleFetcher 自行完成（不依赖 Premium get_text）。

认证流程：
    1. 在 https://www.instapaper.com/main/request_oauth_consumer_token 申请 API Key/Secret
    2. 使用邮箱 + 密码 + API Key/Secret 通过 xAuth 换取 access_token
    3. access_token 长期有效，存储后复用（见 .instapaper_session.json）

文件夹 ID 约定：
    unread   - 稍后阅读（默认收件箱）
    starred  - 星标（收藏夹）
    archive  - 已归档
    <数字>   - 自定义文件夹 ID
"""

import asyncio
import time
from typing import Optional

import httpx
from loguru import logger


# Instapaper xAuth 端点
_XAUTH_URL = "https://www.instapaper.com/api/1/oauth/access_token"
_API_BASE = "https://www.instapaper.com/api/1"


class InstapaperService:
    """
    Instapaper Full API 封装（基于 OAuth 1.0a xAuth）。

    使用方式：
        svc = InstapaperService(consumer_key, consumer_secret)
        await svc.login(email, password)
        bookmarks = await svc.get_bookmarks("starred")
    """

    def __init__(self, consumer_key: str, consumer_secret: str):
        """
        Args:
            consumer_key:    Instapaper API Consumer Key
            consumer_secret: Instapaper API Consumer Secret
        """
        self.consumer_key = consumer_key
        self.consumer_secret = consumer_secret
        self._access_token: Optional[str] = None
        self._access_secret: Optional[str] = None
        self._client = httpx.AsyncClient(timeout=30.0)

    async def close(self) -> None:
        await self._client.aclose()

    # ==================== 认证 ====================

    async def login(self, email: str, password: str) -> dict:
        """
        使用 xAuth 方式登录，获取 access_token/secret。

        xAuth 直接用邮箱+密码换取 OAuth token，无需浏览器跳转。

        Returns:
            {"access_token": ..., "access_secret": ...}
        """
        # 构建 xAuth 参数（OAuth 1.0a 签名）
        params = {
            "x_auth_username": email,
            "x_auth_password": password,
            "x_auth_mode": "client_auth",
        }
        headers = self._build_oauth_header("POST", _XAUTH_URL, params)

        resp = await self._client.post(
            _XAUTH_URL,
            data=params,
            headers=headers,
        )

        if resp.status_code != 200:
            body = resp.text[:300]
            raise RuntimeError(
                f"Instapaper xAuth 失败 (status={resp.status_code}): {body}"
            )

        # 响应格式：oauth_token=xxx&oauth_token_secret=xxx
        token_data = dict(pair.split("=") for pair in resp.text.strip().split("&"))
        self._access_token = token_data.get("oauth_token", "")
        self._access_secret = token_data.get("oauth_token_secret", "")

        if not self._access_token:
            raise RuntimeError(f"xAuth 响应中未找到 token: {resp.text[:200]}")

        logger.info("[Instapaper] xAuth 登录成功")
        return {
            "access_token": self._access_token,
            "access_secret": self._access_secret,
        }

    def set_tokens(self, access_token: str, access_secret: str) -> None:
        """直接设置已有的 access token（复用缓存）"""
        self._access_token = access_token
        self._access_secret = access_secret

    # ==================== API 调用 ====================

    async def verify_credentials(self) -> dict:
        """验证当前 token 是否有效，返回用户信息"""
        data = await self._api_post("/account/verify_credentials")
        user = next((x for x in data if x.get("type") == "user"), {})
        return user

    async def get_folders(self) -> list[dict]:
        """
        获取所有自定义文件夹。

        Returns:
            list of {"folder_id": ..., "title": ...}
        """
        data = await self._api_post("/folders/list")
        return [
            {"folder_id": str(f.get("folder_id", "")), "title": f.get("title", "")}
            for f in data
            if f.get("type") == "folder"
        ]

    async def get_bookmarks(
        self,
        folder: str = "unread",
        limit: int = 500,
    ) -> list[dict]:
        """
        获取指定文件夹的书签列表。

        Args:
            folder: "unread" | "starred" | "archive" | 自定义文件夹ID（字符串）
            limit:  每次最多返回条数（API 上限 500）

        Returns:
            list of bookmark dicts: {bookmark_id, title, url, description, time, folder_id, ...}
        """
        params = {"folder_id": folder, "limit": limit}
        data = await self._api_post("/bookmarks/list", params)
        return [b for b in data if b.get("type") == "bookmark"]

    async def get_all_bookmarks(self, folder: str = "unread") -> list[dict]:
        """
        获取指定文件夹所有书签（自动翻页，以 have 参数实现）。
        """
        all_bookmarks: list[dict] = []
        have_ids: list[str] = []

        while True:
            params: dict = {"folder_id": folder, "limit": 500}
            if have_ids:
                params["have"] = ",".join(have_ids)

            data = await self._api_post("/bookmarks/list", params)
            bookmarks = [b for b in data if b.get("type") == "bookmark"]

            if not bookmarks:
                break

            # 过滤已获取的
            new_bms = [b for b in bookmarks if str(b.get("bookmark_id")) not in have_ids]
            if not new_bms:
                break

            all_bookmarks.extend(new_bms)
            have_ids.extend(str(b.get("bookmark_id")) for b in new_bms)

            # 如果返回数量 < 500，说明没有更多了
            if len(bookmarks) < 500:
                break

            await asyncio.sleep(0.5)

        return all_bookmarks

    # ==================== 内部工具 ====================

    async def _api_post(self, endpoint: str, params: dict = None) -> list:
        """发起已认证的 API POST 请求"""
        if not self._access_token:
            raise RuntimeError("未登录，请先调用 login()")

        url = _API_BASE + endpoint
        post_data = params or {}
        headers = self._build_oauth_header(
            "POST", url, post_data,
            token=self._access_token,
            token_secret=self._access_secret,
        )

        resp = await self._client.post(url, data=post_data, headers=headers)

        if resp.status_code != 200:
            raise RuntimeError(
                f"Instapaper API 错误 {endpoint}: "
                f"status={resp.status_code}, body={resp.text[:200]}"
            )

        return resp.json()

    def _build_oauth_header(
        self,
        method: str,
        url: str,
        params: dict,
        token: str = "",
        token_secret: str = "",
    ) -> dict:
        """构建 OAuth 1.0a HMAC-SHA1 签名的 Authorization 头"""
        import base64
        import hashlib
        import hmac
        import urllib.parse
        import uuid

        oauth_params = {
            "oauth_consumer_key": self.consumer_key,
            "oauth_nonce": uuid.uuid4().hex,
            "oauth_signature_method": "HMAC-SHA1",
            "oauth_timestamp": str(int(time.time())),
            "oauth_version": "1.0",
        }
        if token:
            oauth_params["oauth_token"] = token

        # 合并所有参数用于签名
        all_params = {**params, **oauth_params}
        sorted_params = "&".join(
            f"{urllib.parse.quote(k, safe='')}"
            f"={urllib.parse.quote(str(v), safe='')}"
            for k, v in sorted(all_params.items())
        )
        base_string = (
            f"{method.upper()}"
            f"&{urllib.parse.quote(url, safe='')}"
            f"&{urllib.parse.quote(sorted_params, safe='')}"
        )
        signing_key = (
            f"{urllib.parse.quote(self.consumer_secret, safe='')}"
            f"&{urllib.parse.quote(token_secret, safe='')}"
        )
        signature = base64.b64encode(
            hmac.new(
                signing_key.encode("utf-8"),
                base_string.encode("utf-8"),
                hashlib.sha1,
            ).digest()
        ).decode("utf-8")

        oauth_params["oauth_signature"] = signature
        auth_header = "OAuth " + ", ".join(
            f'{urllib.parse.quote(k, safe="")}="{urllib.parse.quote(str(v), safe="")}"'
            for k, v in sorted(oauth_params.items())
        )
        return {"Authorization": auth_header, "Content-Type": "application/x-www-form-urlencoded"}
