"""Browser-backed authentication for Xiaohongshu's signed web API."""

import asyncio
from dataclasses import dataclass
import json
import os
from pathlib import Path
import select
import sys
import time
from typing import Any, Awaitable, Callable

from loguru import logger

try:
    from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError
except ImportError:  # pragma: no cover - tests inject a Playwright replacement
    Page = Any

    class PlaywrightTimeoutError(Exception):
        pass


_XHS_ORIGIN = "https://www.xiaohongshu.com/"
_QR_CODE_SELECTOR = "xpath=//img[@class='qrcode-img']"
_LOGIN_BUTTON_SELECTOR = "xpath=//*[@id='app']/div[1]/div[2]/div[1]/ul/div[1]/button"
_DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
@dataclass(frozen=True)
class XhsAuthOptions:
    force_login: bool = False
    cookie: str = ""
    env_cookie: str = ""
    interactive: bool = True
    login_timeout: float = 120.0
    headless: bool = True


@dataclass(frozen=True)
class XhsBrowserSession:
    page: Page
    cookie_dict: dict[str, str]
    cookie_header: str
    user_agent: str


@dataclass(frozen=True)
class _XhsReadiness:
    cookie_dict: dict[str, str]
    cookie_records: list[dict[str, Any]]
    missing: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return not self.missing


class XhsAuthenticationError(RuntimeError):
    """Authentication or signing-page setup could not be completed."""


def parse_cookie_string(cookie_string: str) -> dict[str, str]:
    """Parse a browser Cookie header while ignoring malformed segments."""
    cookies: dict[str, str] = {}
    for segment in cookie_string.split(";"):
        name, separator, value = segment.strip().partition("=")
        if separator and name and value:
            cookies[name] = value
    return cookies


def _cookie_header(cookie_dict: dict[str, str]) -> str:
    return "; ".join(f"{name}={value}" for name, value in cookie_dict.items())


async def _default_playwright_factory():
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:  # pragma: no cover - dependency is installed in normal runs
        raise XhsAuthenticationError(
            "Playwright is required for Xiaohongshu login; run `playwright install chromium`."
        ) from exc
    return await async_playwright().start()


def _stdin_confirmation_requested() -> bool:
    if not sys.stdin.isatty():
        return False
    try:
        readable, _, _ = select.select([sys.stdin], [], [], 0)
    except (OSError, ValueError):
        return False
    if not readable:
        return False
    sys.stdin.readline()
    return True


class XhsAuthService:
    """Own the Playwright resources backing one authenticated XHS session."""

    def __init__(
        self,
        options: XhsAuthOptions | None = None,
        *,
        session_file: str | Path = ".xhs_session.json",
        playwright_factory: Callable[[], Awaitable[Any]] = _default_playwright_factory,
        is_interactive: Callable[[], bool] = lambda: sys.stdin.isatty(),
        monotonic: Callable[[], float] = time.monotonic,
        manual_confirmation_requested: Callable[[], bool] = _stdin_confirmation_requested,
    ):
        self.options = options or XhsAuthOptions()
        self.session_file = Path(session_file)
        self._playwright_factory = playwright_factory
        self._is_interactive = is_interactive
        self._monotonic = monotonic
        self._manual_confirmation_requested = manual_confirmation_requested
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._session: XhsBrowserSession | None = None
        self._closed = False

    async def open(self) -> XhsBrowserSession:
        if self._session is not None:
            return self._session
        if self._closed:
            raise XhsAuthenticationError("Xiaohongshu authentication service is closed.")

        mode, cookies = self._resolve_mode()
        if mode == "qr" and not (self.options.interactive and self._is_interactive()):
            raise XhsAuthenticationError(
                "No usable Xiaohongshu session is available. Supply --cookie, "
                "set XHS_COOKIE, or run in an interactive terminal to log in by QR code."
            )

        try:
            self._playwright = await self._playwright_factory()
            page = await self._open_page(mode, cookies)
            await page.goto(_XHS_ORIGIN, wait_until="domcontentloaded")

            initial_web_session = ""
            if mode == "qr":
                await self._prepare_qr_login(page)
                initial_cookies, _ = await self._current_cookies()
                initial_web_session = initial_cookies.get("web_session", "")

            readiness = await self._wait_for_ready_session(
                page,
                qr_mode=mode == "qr",
                initial_web_session=initial_web_session,
            )
            if mode == "qr":
                self._persist_session(readiness.cookie_records)

            user_agent = await page.evaluate("() => navigator.userAgent")
            self._session = XhsBrowserSession(
                page=page,
                cookie_dict=readiness.cookie_dict,
                cookie_header=_cookie_header(readiness.cookie_dict),
                user_agent=user_agent or _DEFAULT_USER_AGENT,
            )
            return self._session
        except XhsAuthenticationError:
            await self.close()
            raise
        except Exception as exc:
            await self.close()
            raise XhsAuthenticationError(f"Unable to initialize Xiaohongshu browser session: {exc}") from exc

    async def _open_page(self, mode: str, cookies: dict[str, str]) -> Page:
        if mode == "qr":
            self._context = await self._playwright.chromium.launch_persistent_context(
                "",
                headless=False,
                user_agent=_DEFAULT_USER_AGENT,
            )
            pages = list(self._context.pages)
            page = pages[0] if pages else await self._context.new_page()
            for extra_page in pages[1:]:
                await extra_page.close()
            return page

        self._browser = await self._playwright.chromium.launch(headless=self.options.headless)
        self._context = await self._browser.new_context(user_agent=_DEFAULT_USER_AGENT)
        if cookies:
            await self._context.add_cookies(self._as_playwright_cookies(cookies))
        return await self._context.new_page()

    async def _prepare_qr_login(self, page: Page) -> None:
        timeout_ms = max(1, min(5000, round(self.options.login_timeout * 1000)))
        try:
            await page.wait_for_selector(_QR_CODE_SELECTOR, timeout=timeout_ms)
            return
        except PlaywrightTimeoutError:
            logger.info("[XHS] QR code is not visible; opening the login dialog.")

        try:
            await page.locator(_LOGIN_BUTTON_SELECTOR).click()
            await page.wait_for_selector(_QR_CODE_SELECTOR, timeout=timeout_ms)
        except PlaywrightTimeoutError as exc:
            raise XhsAuthenticationError(
                "Xiaohongshu QR-code login UI did not appear. "
                "Reload the page and retry."
            ) from exc

    def _resolve_mode(self) -> tuple[str, dict[str, str]]:
        if self.options.force_login:
            return "qr", {}
        if self.options.cookie:
            return "cookie", parse_cookie_string(self.options.cookie)
        env_cookie = self.options.env_cookie or os.getenv("XHS_COOKIE", "")
        if env_cookie:
            return "environment", parse_cookie_string(env_cookie)
        saved_cookies = self._load_saved_cookies()
        if self._has_session_cookies(saved_cookies):
            return "saved", saved_cookies
        if saved_cookies:
            logger.info("[XHS] Saved session is incomplete; starting QR-code login.")
        return "qr", {}

    @staticmethod
    def _has_session_cookies(cookies: dict[str, str]) -> bool:
        return bool(cookies.get("a1") and cookies.get("web_session"))

    def _load_saved_cookies(self) -> dict[str, str]:
        if not self.session_file.is_file():
            return {}
        try:
            contents = json.loads(self.session_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.warning("[XHS] Ignoring unreadable session file: {}", self.session_file)
            return {}
        if isinstance(contents, dict) and isinstance(contents.get("cookies"), list):
            return {
                item["name"]: item["value"]
                for item in contents["cookies"]
                if isinstance(item, dict) and isinstance(item.get("name"), str)
                and isinstance(item.get("value"), str)
            }
        if isinstance(contents, dict) and isinstance(contents.get("cookie_header"), str):
            return parse_cookie_string(contents["cookie_header"])
        return {}

    @staticmethod
    def _as_playwright_cookies(cookies: dict[str, str]) -> list[dict[str, str]]:
        return [
            {
                "name": name,
                "value": value,
                "domain": ".xiaohongshu.com",
                "path": "/",
            }
            for name, value in cookies.items()
        ]

    async def _current_cookies(self) -> tuple[dict[str, str], list[dict[str, Any]]]:
        records = await self._context.cookies()
        cookie_dict = {
            item["name"]: item["value"]
            for item in records
            if isinstance(item, dict) and isinstance(item.get("name"), str)
            and isinstance(item.get("value"), str)
        }
        return cookie_dict, records

    async def _read_readiness(self, page: Page) -> _XhsReadiness:
        cookie_dict, cookie_records = await self._current_cookies()
        has_signing_function = bool(
            await page.evaluate("() => typeof window.mnsv2 === 'function'")
        )
        b1 = await page.evaluate("() => window.localStorage.getItem('b1') || ''")
        missing = tuple(
            name
            for name, present in (
                ("web_session cookie", bool(cookie_dict.get("web_session"))),
                ("a1 cookie", bool(cookie_dict.get("a1"))),
                ("window.mnsv2", has_signing_function),
                ("b1 localStorage", bool(b1)),
            )
            if not present
        )
        return _XhsReadiness(
            cookie_dict=cookie_dict,
            cookie_records=cookie_records,
            missing=missing,
        )

    @staticmethod
    async def _security_verification_visible(page: Page) -> bool:
        value = await page.evaluate(
            """() => {
                const text = document.body?.innerText || '';
                return (
                    text.includes('为保护账号安全')
                    || text.includes('扫码验证身份')
                );
            }"""
        )
        return bool(value)

    async def _wait_for_ready_session(
        self,
        page: Page,
        *,
        qr_mode: bool,
        initial_web_session: str = "",
    ) -> _XhsReadiness:
        if qr_mode:
            logger.info(
                "[XHS] Waiting for interactive QR-code login. "
                "Press Enter after scanning to check immediately."
            )
        deadline = self._monotonic() + max(0.0, self.options.login_timeout)
        while True:
            readiness = await self._read_readiness(page)
            web_session = readiness.cookie_dict.get("web_session", "")
            if readiness.ready and not qr_mode:
                return readiness
            if readiness.ready and qr_mode:
                manual_check = self._manual_confirmation_requested()
                cookie_changed = bool(web_session and web_session != initial_web_session)
                security_verification_visible = await self._security_verification_visible(page)
                if cookie_changed and not security_verification_visible:
                    return readiness
                if security_verification_visible and manual_check:
                    logger.info(
                        "[XHS] Xiaohongshu requires a second security QR scan. "
                        "Use the app account that is already logged in, then press Enter again."
                    )
                    continue
                if manual_check:
                    logger.info(
                        "[XHS] Login is not confirmed yet; keep the QR page open and finish scanning."
                    )

            remaining = deadline - self._monotonic()
            if remaining <= 0:
                if readiness.ready and qr_mode:
                    raise XhsAuthenticationError(
                        "Xiaohongshu QR-code login was not confirmed before the "
                        f"{self.options.login_timeout:g}-second timeout. "
                        "Scan the QR code completely, then press Enter to check again."
                    )
                missing = ", ".join(readiness.missing)
                raise XhsAuthenticationError(
                    "Xiaohongshu browser session was not ready before timeout; "
                    f"missing: {missing}. Refresh the session or log in again."
                )
            await page.wait_for_timeout(min(250, max(1, round(remaining * 1000))))

    def _persist_session(self, cookies: list[dict[str, Any]]) -> None:
        self.session_file.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"cookies": cookies}, ensure_ascii=False, indent=2)
        descriptor = os.open(self.session_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
        os.chmod(self.session_file, 0o600)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for resource, method in (
            (self._context, "close"),
            (self._browser, "close"),
            (self._playwright, "stop"),
        ):
            if resource is not None:
                try:
                    await getattr(resource, method)()
                except Exception as exc:  # pragma: no cover - cleanup should not hide the original error
                    logger.debug("[XHS] Failed to close browser resource: {}", exc)
        self._context = self._browser = self._playwright = None
        self._session = None

    async def __aenter__(self) -> XhsBrowserSession:
        return await self.open()

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()
