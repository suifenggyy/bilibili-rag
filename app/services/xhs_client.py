"""Signed, reusable HTTP transport for Xiaohongshu's web endpoints."""

import asyncio
import hashlib
import json
import os
from pathlib import Path
import random
import time
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx

from app.services.xhs_auth import XhsBrowserSession
from app.services.xhs_models import XhsNoteReference


_API_ORIGIN = "https://edith.xiaohongshu.com"
_WEB_ORIGIN = "https://www.xiaohongshu.com"
_BASE64_CHARS = "ZmserbBoHQtNP+wOcza/LpngG8yJq42KWYj0DSfdikx3VT16IlUAFM97hECvuRX5"
_IP_BLOCK_CODES = {300012, 300013, 300014}
_NOTE_UNAVAILABLE_CODES = {-510001, -510002}
_AUTHENTICATION_CODES = {-101, -100, 100001, 100003, 300010}
_TRANSIENT_STATUS_CODES = {500, 502, 503, 504}
_TRUSTED_XHS_HOSTS = {
    "www.xiaohongshu.com",
    "xiaohongshu.com",
    "edith.xiaohongshu.com",
}
_XHS_API_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Priority": "u=1, i",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
}


class XhsClientError(RuntimeError):
    """Base error for the Xiaohongshu signed transport."""


class XhsRiskControlError(XhsClientError):
    """The request triggered CAPTCHA, IP blocking, or anti-bot protection."""


class XhsAuthenticationError(XhsClientError):
    """The API rejected the authenticated browser session."""


class XhsDataFetchError(XhsClientError):
    """The server returned a malformed or otherwise unusable response."""


class XhsNoteUnavailableError(XhsDataFetchError):
    """The requested note was deleted, private, or otherwise unavailable."""


def _custom_b64(value: bytes) -> str:
    encoded: list[str] = []
    for index in range(0, len(value), 3):
        block = value[index:index + 3]
        numeric = int.from_bytes(block, "big") << (3 - len(block)) * 8
        encoded.append(_BASE64_CHARS[(numeric >> 18) & 63])
        encoded.append(_BASE64_CHARS[(numeric >> 12) & 63])
        encoded.append(_BASE64_CHARS[(numeric >> 6) & 63] if len(block) > 1 else "=")
        encoded.append(_BASE64_CHARS[numeric & 63] if len(block) > 2 else "=")
    return "".join(encoded)


def _mrc(value: str) -> int:
    """The short CRC variant used by the current XHS web client."""
    crc = 0xFFFFFFFF
    for char in value[:57]:
        crc ^= ord(char)
        for _ in range(8):
            crc = (crc >> 1) ^ (0xEDB88320 if crc & 1 else 0)
    return (crc ^ 0xFFFFFFFF ^ 0xEDB88320) & 0xFFFFFFFF


def _encode_query(params: dict[str, Any]) -> str:
    return str(httpx.QueryParams(params))


def _is_trusted_xhs_url(url: str) -> bool:
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and (
        hostname in _TRUSTED_XHS_HOSTS or hostname.endswith(".xiaohongshu.com")
    )


def _sign_string(
    uri: str,
    data: dict[str, Any] | None,
    method: str,
    *,
    query_string: str = "",
) -> str:
    if method == "POST":
        return uri + (json.dumps(data, separators=(",", ":"), ensure_ascii=False) if data else "")
    return f"{uri}?{query_string}"


def _x_s_common(a1: str, b1: str, x_s: str, x_t: str) -> str:
    payload = {
        "s0": 3,
        "s1": "",
        "x0": "1",
        "x1": "4.2.2",
        "x2": "Mac OS",
        "x3": "xhs-pc-web",
        "x4": "4.74.0",
        "x5": a1,
        "x6": x_t,
        "x7": x_s,
        "x8": b1,
        "x9": _mrc(x_t + x_s + b1),
        "x10": 154,
        "x11": "normal",
    }
    return _custom_b64(json.dumps(payload, separators=(",", ":")).encode())


def _x_s_payload(signature: str, data: dict[str, Any] | None) -> str:
    payload = {
        "x0": "4.2.1",
        "x1": "xhs-pc-web",
        "x2": "Mac OS",
        "x3": signature,
        "x4": "object" if isinstance(data, dict) else "string",
    }
    return "XYS_" + _custom_b64(json.dumps(payload, separators=(",", ":")).encode())


class XhsClient:
    """Provide serialized browser signing and signed XHS API requests."""

    def __init__(
        self,
        session: XhsBrowserSession,
        *,
        http_client: httpx.AsyncClient | None = None,
        request_interval: float = 1.0,
        max_media_bytes: int = 500 * 1024 * 1024,
    ):
        self.session = session
        self.request_interval = max(0.0, request_interval)
        self.max_media_bytes = max_media_bytes
        self._client = http_client or httpx.AsyncClient(
            timeout=30.0,
            follow_redirects=True,
        )
        self._signing_lock = asyncio.Lock()
        self._request_lock = asyncio.Lock()
        self._last_request_at = 0.0
        self._closed = False

    def _browser_headers(self, *, authenticated: bool) -> dict[str, str]:
        headers = {**_XHS_API_HEADERS, "User-Agent": self.session.user_agent}
        if authenticated:
            headers.update(
                {
                    "Cookie": self.session.cookie_header,
                    "Origin": _WEB_ORIGIN,
                    "Referer": f"{_WEB_ORIGIN}/",
                }
            )
        return headers

    async def build_signed_headers(
        self,
        uri: str,
        *,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        query_string: str | None = None,
    ) -> dict[str, str]:
        if (params is None) == (payload is None):
            raise ValueError("provide exactly one of params or payload")
        method = "GET" if params is not None else "POST"
        data = params if params is not None else payload
        if method == "GET":
            query_string = _encode_query(params or {}) if query_string is None else query_string
        signing_input = _sign_string(
            uri,
            data,
            method,
            query_string=query_string or "",
        )
        md5_value = hashlib.md5(signing_input.encode("utf-8")).hexdigest()

        async with self._signing_lock:
            try:
                native_headers = await self.session.page.evaluate(
                    """sign_string => {
                        if (typeof window._webmsxyw !== 'function') {
                            return null;
                        }
                        return window._webmsxyw(sign_string);
                    }""",
                    signing_input,
                )
            except Exception as exc:
                raise XhsClientError(f"Xiaohongshu signing function failed: {exc}") from exc

        if not self.session.cookie_dict.get("a1"):
            raise XhsAuthenticationError(
                "Xiaohongshu signing state is incomplete; refresh the a1 cookie and b1 localStorage."
            )
        if isinstance(native_headers, dict):
            x_s = native_headers.get("X-s")
            x_t = native_headers.get("X-t")
            if isinstance(x_s, str) and x_s and x_t is not None:
                return {
                    "X-S": x_s,
                    "X-T": str(x_t),
                    "X-B3-Traceid": "".join(
                        random.choice("abcdef0123456789") for _ in range(16)
                    ),
                }

        async with self._signing_lock:
            try:
                signature = await self.session.page.evaluate(
                    "({sign_string, md5}) => window.mnsv2(sign_string, md5)",
                    {"sign_string": signing_input, "md5": md5_value},
                )
                b1 = await self.session.page.evaluate(
                    "() => window.localStorage.getItem('b1') || ''"
                )
            except Exception as exc:
                raise XhsClientError(f"Xiaohongshu signing function failed: {exc}") from exc

        if not isinstance(signature, str) or not signature:
            raise XhsClientError("Xiaohongshu signing function returned an empty signature.")
        a1 = self.session.cookie_dict.get("a1", "")
        if not a1 or not b1:
            raise XhsAuthenticationError(
                "Xiaohongshu signing state is incomplete; refresh the a1 cookie and b1 localStorage."
            )
        x_s = _x_s_payload(signature, data)
        x_t = str(int(time.time() * 1000))
        return {
            "X-S": x_s,
            "X-T": x_t,
            "X-S-Common": _x_s_common(a1, str(b1), x_s, x_t),
            "X-B3-Traceid": "".join(random.choice("abcdef0123456789") for _ in range(16)),
        }

    async def _apply_interval(self) -> None:
        if not self.request_interval:
            return
        async with self._request_lock:
            delay = self.request_interval - (time.monotonic() - self._last_request_at)
            if delay > 0:
                await asyncio.sleep(delay)
            self._last_request_at = time.monotonic()

    async def _request_json(
        self,
        method: str,
        uri: str,
        *,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        query_string = _encode_query(params or {}) if method == "GET" else ""
        headers = (
            await self.build_signed_headers(uri, params=params, query_string=query_string)
            if method == "GET"
            else await self.build_signed_headers(uri, payload=payload)
        )
        headers = {
            **self._browser_headers(authenticated=True),
            **headers,
            "Content-Type": "application/json;charset=UTF-8",
        }
        request_url = f"{_API_ORIGIN}{uri}"
        if method == "GET":
            request_url = f"{request_url}?{query_string}"
        for attempt in range(3):
            await self._apply_interval()
            try:
                response = await self._client.request(
                    method,
                    request_url,
                    content=(json.dumps(payload, separators=(",", ":"), ensure_ascii=False) if payload is not None else None),
                    headers=headers,
                    follow_redirects=False,
                )
            except httpx.TransportError as exc:
                if attempt == 2:
                    raise XhsDataFetchError(f"Xiaohongshu transport failed after 3 attempts: {exc}") from exc
                await asyncio.sleep(0.25 * (attempt + 1))
                continue
            if response.status_code in (461, 471):
                raise XhsRiskControlError(f"Xiaohongshu CAPTCHA/risk-control response: HTTP {response.status_code}")
            if response.status_code in _TRANSIENT_STATUS_CODES and attempt < 2:
                await asyncio.sleep(0.25 * (attempt + 1))
                continue
            if response.status_code in (401, 403):
                raise XhsAuthenticationError(f"Xiaohongshu rejected the session: HTTP {response.status_code}")
            if 300 <= response.status_code < 400:
                raise XhsDataFetchError(
                    "Xiaohongshu signed API request returned a redirect; "
                    "refusing to forward signing headers."
                )
            if response.status_code < 200 or response.status_code >= 300:
                raise XhsDataFetchError(f"Xiaohongshu API request failed: HTTP {response.status_code}")
            return self._parse_api_response(response)
        raise AssertionError("unreachable")

    async def _request_browser_json(
        self,
        method: str,
        uri: str,
        *,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if (params is None) == (payload is None):
            raise ValueError("provide exactly one of params or payload")
        query_string = _encode_query(params or {}) if method == "GET" else ""
        signing_input = _sign_string(
            uri,
            params if method == "GET" else payload,
            method,
            query_string=query_string,
        )
        body = (
            json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
            if payload is not None
            else ""
        )
        try:
            result = await self.session.page.evaluate(
                """async ({ method, signing_input, body }) => {
                    if (typeof window._webmsxyw !== 'function') {
                        throw new Error('Xiaohongshu native signing function is unavailable.');
                    }
                    const signed = window._webmsxyw(signing_input);
                    const response = await fetch(
                        `https://edith.xiaohongshu.com${signing_input}`,
                        {
                            method,
                            credentials: 'include',
                            headers: {
                                'Content-Type': 'application/json;charset=UTF-8',
                                'X-S': signed['X-s'],
                                'X-T': String(signed['X-t']),
                            },
                            body: body || undefined,
                        },
                    );
                    return { status: response.status, body: await response.text() };
                }""",
                {"method": method, "signing_input": signing_input, "body": body},
            )
        except Exception as exc:
            raise XhsDataFetchError(
                f"Xiaohongshu browser request failed: {exc}"
            ) from exc
        if not isinstance(result, dict) or not isinstance(result.get("status"), int):
            raise XhsDataFetchError("Xiaohongshu browser request returned an invalid response.")
        response = httpx.Response(result["status"], content=str(result.get("body", "")))
        self._raise_for_non_api_status(response, "browser API request")
        return self._parse_api_response(response)

    @staticmethod
    def _parse_api_response(response: httpx.Response) -> dict[str, Any]:
        try:
            body = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise XhsDataFetchError("Xiaohongshu API returned malformed JSON.") from exc
        if not isinstance(body, dict):
            raise XhsDataFetchError("Xiaohongshu API returned a non-object JSON response.")

        success = body.get("success")
        code = body.get("code", body.get("error_code", 0))
        if success is True or (success is None and code == 0):
            data = body.get("data", body)
            if not isinstance(data, dict):
                raise XhsDataFetchError("Xiaohongshu API response data is malformed.")
            return data

        try:
            numeric_code = int(code)
        except (TypeError, ValueError):
            numeric_code = None
        message = str(body.get("msg") or body.get("message") or body.get("description") or "")
        lowered = message.lower()
        if numeric_code in _IP_BLOCK_CODES or any(term in lowered for term in ("captcha", "verify", "risk", "ip block", "风控", "验证码", "访问频繁")):
            raise XhsRiskControlError(message or f"Xiaohongshu risk-control code: {code}")
        if numeric_code in _NOTE_UNAVAILABLE_CODES or any(term in lowered for term in ("note status abnormal", "note unavailable", "笔记不存在", "已删除", "不可见")):
            raise XhsNoteUnavailableError(message or f"Xiaohongshu note unavailable code: {code}")
        authentication_terms = (
            "login",
            "authentication",
            "not logged",
            "permission",
            "未登录",
            "登录",
            "没有权限访问",
            "无权限",
        )
        if numeric_code in _AUTHENTICATION_CODES or any(
            term in lowered for term in authentication_terms
        ):
            detail = message or f"Xiaohongshu authentication code: {code}"
            raise XhsAuthenticationError(
                f"{detail}. Refresh the session by rerunning the exporter with --login."
            )
        raise XhsDataFetchError(message or f"Xiaohongshu API error code: {code}")

    async def _request_html(self, url: str, *, authenticated: bool) -> str:
        if authenticated and not _is_trusted_xhs_url(url):
            raise XhsDataFetchError(
                "Refusing to send Xiaohongshu session cookies to an untrusted host."
            )
        headers = self._browser_headers(authenticated=authenticated)
        response = await self._request_response_with_retry("GET", url, headers=headers)
        self._raise_for_non_api_status(response, "HTML request")
        return response.text

    async def _request_response_with_retry(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
    ) -> httpx.Response:
        for attempt in range(3):
            await self._apply_interval()
            try:
                response = await self._client.request(
                    method,
                    url,
                    headers=headers,
                    follow_redirects=True,
                )
            except httpx.TransportError as exc:
                if attempt == 2:
                    raise XhsDataFetchError(
                        f"Xiaohongshu request failed after 3 attempts: {exc}"
                    ) from exc
                await asyncio.sleep(0.25 * (attempt + 1))
                continue
            if response.status_code in _TRANSIENT_STATUS_CODES and attempt < 2:
                await asyncio.sleep(0.25 * (attempt + 1))
                continue
            return response
        raise AssertionError("unreachable")

    @staticmethod
    def _raise_for_non_api_status(response: httpx.Response, operation: str) -> None:
        if response.status_code in (461, 471):
            raise XhsRiskControlError(
                f"Xiaohongshu CAPTCHA/risk-control response: HTTP {response.status_code}"
            )
        if response.status_code in (401, 403):
            raise XhsAuthenticationError(
                f"Xiaohongshu rejected the session: HTTP {response.status_code}"
            )
        if response.status_code < 200 or response.status_code >= 400:
            raise XhsDataFetchError(
                f"Xiaohongshu {operation} failed: HTTP {response.status_code}"
            )

    async def search_notes(self, keyword: str, page: int, page_size: int, search_id: str) -> dict[str, Any]:
        return await self._request_json(
            "POST",
            "/api/sns/web/v1/search/notes",
            payload={
                "keyword": keyword,
                "page": page,
                "page_size": page_size,
                "search_id": search_id,
                "sort": "general",
                "note_type": 0,
            },
        )

    async def get_note_detail(self, reference: XhsNoteReference) -> dict[str, Any]:
        return await self._request_json(
            "POST",
            "/api/sns/web/v1/feed",
            payload={
                "source_note_id": reference.note_id,
                "image_formats": ["jpg", "webp", "avif"],
                "extra": {"need_body_topic": 1},
                "xsec_source": reference.xsec_source or "pc_search",
                "xsec_token": reference.xsec_token,
            },
        )

    async def get_note_html(self, reference: XhsNoteReference, *, authenticated: bool) -> str:
        url = reference.source_url or f"{_WEB_ORIGIN}/explore/{reference.note_id}"
        if reference.xsec_token:
            url = _note_url_with_security_params(
                url,
                reference.xsec_token,
                reference.xsec_source or "pc_search",
            )
        return await self._request_html(url, authenticated=authenticated)

    async def get_creator_profile_html(
        self, user_id: str, xsec_token: str = "", xsec_source: str = ""
    ) -> str:
        params = {
            key: value
            for key, value in {"xsec_token": xsec_token, "xsec_source": xsec_source}.items()
            if value
        }
        suffix = f"?{urlencode(params)}" if params else ""
        return await self._request_html(f"{_WEB_ORIGIN}/user/profile/{user_id}{suffix}", authenticated=True)

    async def get_creator_notes(
        self, user_id: str, cursor: str, xsec_token: str, xsec_source: str
    ) -> dict[str, Any]:
        return await self._request_json(
            "GET",
            "/api/sns/web/v1/user_posted",
            params={
                "num": 30,
                "cursor": cursor,
                "user_id": user_id,
                "xsec_token": xsec_token,
                "xsec_source": xsec_source,
            },
        )

    async def get_current_user(self) -> dict[str, Any]:
        try:
            browser_user = await self.session.page.evaluate(
                """() => {
                    const user = window.__INITIAL_STATE__?.user?.userInfo;
                    const value = user?._rawValue || user?._value || {};
                    return {
                        user_id: value.userId || value.user_id || '',
                        nickname: value.nickname || '',
                    };
                }"""
            )
            if isinstance(browser_user, dict) and browser_user.get("user_id"):
                return browser_user
        except Exception:
            pass
        try:
            return await self._request_json(
                "GET",
                "/api/sns/web/v1/user/selfinfo",
                params={},
            )
        except XhsDataFetchError as exc:
            if "HTTP 406" not in str(exc):
                raise
            return await self._request_json(
                "POST",
                "/api/sns/web/v1/user/selfinfo",
                payload={},
            )

    async def get_user_collect_notes(
        self,
        user_id: str,
        cursor: str = "",
    ) -> dict[str, Any]:
        return await self._request_browser_json(
            "GET",
            "/api/sns/web/v2/note/collect/page",
            params={"user_id": user_id, "num": 30, "cursor": cursor},
        )

    async def get_comments(self, note_id: str, xsec_token: str, cursor: str = "") -> dict[str, Any]:
        return await self._request_json(
            "GET",
            "/api/sns/web/v2/comment/page",
            params={
                "note_id": note_id,
                "cursor": cursor,
                "top_comment_id": "",
                "image_formats": "jpg,webp,avif",
                "xsec_token": xsec_token,
            },
        )

    async def resolve_shared_url(self, url: str) -> str:
        response = await self._request_response_with_retry(
            "GET",
            url,
            headers=self._browser_headers(authenticated=False),
        )
        self._raise_for_non_api_status(response, "shared URL resolution")
        return str(response.url)

    async def download_media(self, url: str, destination: Path) -> Path:
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_suffix(destination.suffix + ".part")
        partial.unlink(missing_ok=True)
        total = 0
        declared_size: int | None = None
        try:
            for attempt in range(3):
                total = 0
                declared_size = None
                try:
                    async with self._client.stream(
                        "GET",
                        url,
                        headers=self._browser_headers(authenticated=False),
                    ) as response:
                        if response.status_code in _TRANSIENT_STATUS_CODES and attempt < 2:
                            await asyncio.sleep(0.25 * (attempt + 1))
                            continue
                        self._raise_for_non_api_status(response, "media request")
                        if response.status_code not in (200, 206):
                            raise XhsDataFetchError(
                                f"Xiaohongshu media request failed: HTTP {response.status_code}"
                            )
                        content_length = response.headers.get("content-length")
                        if content_length:
                            try:
                                declared_size = int(content_length)
                            except ValueError as exc:
                                raise XhsDataFetchError(
                                    "Xiaohongshu media returned an invalid content length."
                                ) from exc
                            if declared_size > self.max_media_bytes:
                                raise XhsDataFetchError(
                                    "Xiaohongshu media exceeds the configured size limit."
                                )
                        with partial.open("wb") as handle:
                            async for chunk in response.aiter_bytes():
                                total += len(chunk)
                                if total > self.max_media_bytes:
                                    raise XhsDataFetchError(
                                        "Xiaohongshu media exceeds the configured size limit."
                                    )
                                handle.write(chunk)
                    break
                except httpx.TransportError as exc:
                    partial.unlink(missing_ok=True)
                    if attempt == 2:
                        raise XhsDataFetchError(
                            f"Xiaohongshu media download failed after 3 attempts: {exc}"
                        ) from exc
                    await asyncio.sleep(0.25 * (attempt + 1))
            else:
                raise AssertionError("unreachable")
            if total == 0:
                raise XhsDataFetchError("Xiaohongshu media download was empty.")
            if declared_size is not None and total != declared_size:
                raise XhsDataFetchError("Xiaohongshu media download size did not match its content length.")
            os.replace(partial, destination)
            return destination
        except OSError as exc:
            raise XhsDataFetchError(f"Xiaohongshu media download failed: {exc}") from exc
        finally:
            partial.unlink(missing_ok=True)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._client.aclose()

    async def __aenter__(self) -> "XhsClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()


def _note_url_with_security_params(url: str, token: str, source: str) -> str:
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.update({"xsec_token": token, "xsec_source": source})
    return urlunparse(parsed._replace(query=urlencode(query)))
