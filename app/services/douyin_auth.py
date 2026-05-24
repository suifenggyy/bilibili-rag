"""
抖音 Web QR 码登录服务

流程：
  1. generate_qrcode()  → 调用 SSO，生成二维码（token + base64 图片）
  2. 用户用抖音 APP 扫码并确认
  3. poll_qrcode_status(token)  → 轮询 SSO 状态
     - waiting  → 继续轮询
     - scanned  → 已扫，等待确认
     - confirmed → 跟随 redirect_url 提取 Cookie，返回 cookie_str
     - expired  → 需重新生成

Douyin SSO endpoints（非官方，反向自网页端）：
  GET https://sso.douyin.com/get_qrcode/
  GET https://sso.douyin.com/check_qrconnect/
"""

import base64
import io
from typing import Optional

import httpx
import qrcode
from loguru import logger


_SSO_BASE = "https://sso.douyin.com"
_DOUYIN_HOME = "https://www.douyin.com"
_AID = "6383"

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.douyin.com/",
    "Origin": "https://www.douyin.com",
}

_QR_STATUS_MAP = {
    1: ("waiting", "等待扫码"),
    2: ("scanned", "已扫码，请在 APP 上确认"),
    3: ("confirmed", "登录成功"),
    4: ("expired", "二维码已过期，请刷新"),
}


def _make_qr_image(url: str) -> str:
    """将 URL 编码为二维码并返回 base64 Data URI。"""
    qr = qrcode.QRCode(version=1, box_size=8, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    return f"data:image/png;base64,{b64}"


def _cookies_to_str(jar: httpx.Cookies) -> str:
    """将 httpx Cookies 转为 key=value; 格式的字符串。"""
    return "; ".join(f"{k}={v}" for k, v in jar.items())


class DouyinAuthService:
    """抖音网页端 QR 扫码登录。"""

    def __init__(self, timeout: float = 10.0):
        self._client = httpx.AsyncClient(
            timeout=timeout,
            headers=_BROWSER_HEADERS,
            follow_redirects=True,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.close()

    # ===================== 生成二维码 =====================

    async def generate_qrcode(self) -> dict:
        """
        向 Douyin SSO 申请二维码。

        Returns:
            {
                "token": str,                   # 用于轮询
                "qrcode_url": str,              # 二维码内容 URL
                "qrcode_image_base64": str,     # data:image/png;base64,…
            }
        """
        resp = await self._client.get(
            f"{_SSO_BASE}/get_qrcode/",
            params={
                "service": _DOUYIN_HOME,
                "need_logo": "false",
                "need_short_url": "true",
                "sdk_version": "2.2.7-tiktok",
                "aid": _AID,
                "account_sdk_source": "sso",
                "language": "zh",
            },
        )
        resp.raise_for_status()
        data = resp.json()

        error_code = data.get("error_code") or data.get("errorCode") or 0
        if error_code != 0:
            msg = data.get("description") or data.get("message") or "未知错误"
            raise RuntimeError(f"获取抖音二维码失败: error_code={error_code}, {msg}")

        inner = data.get("data") or data
        qr_url = (
            inner.get("qrcode_index_url")
            or inner.get("url")
            or inner.get("qr_url")
            or ""
        )
        token = inner.get("token") or inner.get("qrcode_token") or ""

        if not qr_url or not token:
            raise RuntimeError(
                f"抖音 SSO 返回字段不完整: keys={list(inner.keys())}"
            )

        logger.info("[DouyinAuth] 二维码已生成, token={}", token[:16] + "...")
        return {
            "token": token,
            "qrcode_url": qr_url,
            "qrcode_image_base64": _make_qr_image(qr_url),
        }

    # ===================== 轮询状态 =====================

    async def poll_qrcode_status(self, token: str) -> dict:
        """
        轮询二维码登录状态。

        Returns:
            {
                "status":     "waiting" | "scanned" | "confirmed" | "expired",
                "message":    str,
                "cookie_str": str | None,  # 仅 confirmed 时有值
            }
        """
        resp = await self._client.get(
            f"{_SSO_BASE}/check_qrconnect/",
            params={
                "token": token,
                "service": _DOUYIN_HOME,
                "need_logo": "false",
                "aid": _AID,
            },
        )
        resp.raise_for_status()
        data = resp.json()

        error_code = data.get("error_code") or data.get("errorCode") or 0
        if error_code != 0:
            msg = data.get("description") or "未知错误"
            raise RuntimeError(f"轮询抖音二维码状态失败: error_code={error_code}, {msg}")

        inner = data.get("data") or data
        status_code: int = int(inner.get("status") or 1)
        status, message = _QR_STATUS_MAP.get(status_code, ("waiting", "等待扫码"))

        result: dict = {"status": status, "message": message, "cookie_str": None}

        if status == "confirmed":
            redirect_url: str = inner.get("redirect_url") or ""
            cookie_str = await self._extract_cookies(redirect_url, resp.cookies)
            result["cookie_str"] = cookie_str
            logger.info("[DouyinAuth] 登录成功，Cookie 长度={}", len(cookie_str))

        return result

    # ===================== 提取 Cookie =====================

    async def _extract_cookies(
        self,
        redirect_url: str,
        sso_cookies: httpx.Cookies,
    ) -> str:
        """
        跟随 redirect_url 完成登录，从 Set-Cookie 中提取 Douyin Cookie。
        若 redirect_url 为空则直接使用 SSO cookies。
        """
        all_cookies: dict[str, str] = dict(sso_cookies)

        if redirect_url:
            try:
                r = await self._client.get(redirect_url, follow_redirects=True)
                all_cookies.update(dict(r.cookies))
                # 最终落地在 douyin.com，收集 cookies
                for h_cookie in r.headers.get_list("set-cookie"):
                    k, _, rest = h_cookie.partition("=")
                    v = rest.split(";")[0]
                    if k.strip():
                        all_cookies[k.strip()] = v.strip()
            except Exception as e:
                logger.warning("[DouyinAuth] 跟随 redirect_url 失败，使用 SSO cookies: {}", e)

        cookie_str = "; ".join(f"{k}={v}" for k, v in all_cookies.items())
        return cookie_str


    @staticmethod
    def validate_cookie_str(cookie_str: str) -> bool:
        """粗略检查 cookie_str 是否包含抖音必要字段。"""
        required = ["sessionid", "odin_tt"]
        lower = cookie_str.lower()
        return any(k in lower for k in required)
