"""Bridge to the v5 platform adapter, native signer and browser transport."""

import sys
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import unquote, urlsplit

from loguru import logger

if TYPE_CHECKING:
    from dtk.platforms.douyin.adapter import DouyinAdapter

_SUBMODULE_ROOT = Path(__file__).resolve().parents[2] / "douyin_tiktok_download_api" / "src"
_SUBMODULE_ROOT_STR = str(_SUBMODULE_ROOT)
if _SUBMODULE_ROOT_STR not in sys.path:
    sys.path.append(_SUBMODULE_ROOT_STR)


@lru_cache(maxsize=1)
def _get_adapter() -> "DouyinAdapter":
    from dtk.platforms.douyin.adapter import DouyinAdapter
    from dtk.signing.native.signer import NativeSigner  # noqa: F401
    from dtk.transport.wreq_transport import WreqTransport  # noqa: F401
    return DouyinAdapter()


def is_available() -> bool:
    """Check library dependencies without network IO or server initialization."""
    if sys.version_info < (3, 12):
        logger.warning("[DouyinBridge] v5 requires Python 3.12+")
        return False
    try:
        _get_adapter()
        return True
    except ImportError as exc:
        logger.warning("[DouyinBridge] v5 dependency unavailable: {}", exc)
        return False


async def _request(cookie: str, endpoint: str, **params: str | int) -> dict:
    from app.config import settings
    from dtk.core.types import BrowserFamily, Outcome, Platform
    from dtk.platforms.base import browser_version, operating_system
    from dtk.signing.base import RequestSpec as SigningRequest, SigningSession, StaticFingerprint
    from dtk.signing.native.signer import NativeSigner
    from dtk.transport.base import Fingerprint, RequestSpec, TransportFailure, TransportIdentity
    from dtk.transport.classify import Classifier
    from dtk.transport.wreq_transport import WreqTransport

    adapter = _get_adapter()
    # dtk.identity imports the server DB/pool stack; this bridge needs only a header.
    raw_cookie = cookie.strip()
    if raw_cookie.lower().startswith("cookie:"):
        raw_cookie = raw_cookie.partition(":")[2].strip()
    cookies: dict[str, str] = {}
    for part in raw_cookie.split(";"):
        if not part.strip():
            continue
        name, separator, value = part.partition("=")
        if not separator or not name.strip():
            raise ValueError("Invalid Douyin Cookie format; expected name=value; ...")
        cookies[name.strip()] = value.strip()
    if cookie.strip() and not cookies:
        raise ValueError("Invalid Douyin Cookie format; expected name=value; ...")
    user_agent = settings.douyin_user_agent
    family = None
    major = None
    for browser, candidate in (
        ("Chrome", BrowserFamily.CHROME), ("Firefox", BrowserFamily.FIREFOX),
        ("Safari", BrowserFamily.SAFARI),
    ):
        version = browser_version(user_agent, browser)
        if version:
            family, major = candidate, int(version.split(".")[0])
            break
    operating = operating_system(user_agent)
    browser_platform = {
        "Mac OS": "MacIntel", "Mac OS X": "MacIntel",
        "Windows": "Win32", "Linux": "Linux x86_64",
    }.get(operating[0] if operating else "")
    if family is None or major is None or browser_platform is None:
        raise ValueError("DOUYIN_USER_AGENT must describe a supported desktop browser")

    def cookie_number(name: str, default: int) -> int:
        value = unquote(cookies.get(name, "")).strip('"')
        return int(value) if value.isdecimal() and int(value) > 0 else default

    fingerprint = Fingerprint(
        browser_family=family,
        browser_major=major,
        user_agent=user_agent,
        platform=browser_platform,
        screen=f"{cookie_number('dy_swidth', 1920)}x{cookie_number('dy_sheight', 1080)}",
        language="zh-CN",
        timezone="Asia/Shanghai",
        hardware_concurrency=cookie_number("device_web_cpu_core", 12),
        device_memory=cookie_number("device_web_memory_size", 8),
    )
    request = adapter.build_request(
        endpoint, profile=adapter.profile_for(fingerprint), **params
    )
    base = urlsplit(settings.douyin_api_base_url)
    if base.scheme != "https" or base.netloc not in ("www.douyin.com", "www-hj.douyin.com"):
        raise ValueError("DOUYIN_API_BASE_URL must be an HTTPS Douyin API host")
    request["url"] = f"{base.scheme}://{base.netloc}{urlsplit(request['url']).path}"
    signed = await NativeSigner(Platform.DOUYIN).sign(
        SigningRequest.of(request), StaticFingerprint.of(fingerprint),
        SigningSession(cookies=cookies),
    )
    # Send the signer's query and the adapter's body verbatim: both are hashed.
    spec = RequestSpec(
        url=f"{request['url']}?{signed.query}",
        method=request["method"],
        headers={**request["headers"], **signed.headers},
        body=request["body"],
        endpoint=endpoint,
    )
    identity = TransportIdentity(
        id="douyin-request", platform=Platform.DOUYIN, fingerprint=fingerprint, cookies=cookies,
    )
    transport = WreqTransport()
    try:
        response = await transport.request(identity, spec, timeout=30.0)
    except TransportFailure as exc:
        raise RuntimeError(
            f"Douyin {endpoint}: network request failed ({exc.elapsed_ms} ms)"
        ) from None
    finally:
        await transport.close()

    classification = Classifier().classify(response)
    if not response.ok or classification.outcome is not Outcome.OK:
        # Never expose signed URLs, response bodies or cookie values in errors.
        raise RuntimeError(
            f"Douyin {endpoint}: HTTP {response.status}, {classification.rule}; "
            "request rejected, not necessarily an expired Cookie"
        )
    result = response.json()
    if not isinstance(result, dict):
        raise RuntimeError(f"Douyin {endpoint}: expected a JSON object")
    return result


async def fetch_collection_videos(cookie: str, cursor: int = 0, count: int = 20) -> dict:
    return await _request(
        cookie, "douyin.author_bookmarks", cursor=str(cursor), count=count,
    )


async def fetch_user_post_videos(
    sec_user_id: str, max_cursor: int = 0, count: int = 20, cookie: str = "",
) -> dict:
    return await _request(
        cookie, "douyin.author_posts", sec_user_id=sec_user_id,
        cursor=str(max_cursor), count=count,
    )


async def fetch_comments(cookie: str, aweme_id: str, count: int = 20) -> dict:
    return await _request(
        cookie, "douyin.comments", aweme_id=aweme_id, cursor="0", count=count,
    )
