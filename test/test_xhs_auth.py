import json
import os
from pathlib import Path
import unittest

from app.config import settings
from app.services.xhs_auth import (
    XhsAuthOptions,
    XhsAuthService,
    XhsAuthenticationError,
    parse_cookie_string,
)


class FakePage:
    def __init__(self, context, qr_cookies):
        self.context = context
        self.qr_cookies = qr_cookies
        self.qr_login_waited = False

    async def goto(self, _url, **_kwargs):
        return None

    async def evaluate(self, expression):
        if "为保护账号安全" in expression:
            return (
                self.context.security_verification_from
                <= self.context.wait_count
                < self.context.security_verification_until
            )
        if "mnsv2" in expression:
            return self.context.wait_count >= self.context.mnsv2_ready_after
        if "navigator.userAgent" in expression:
            return "fake-agent"
        if "localStorage" in expression:
            if self.context.wait_count >= self.context.b1_ready_after:
                return "fake-b1"
            return ""
        return False

    async def wait_for_selector(self, _selector, **_kwargs):
        if not self.context.qr_visible:
            raise self.context.playwright_timeout("QR code not visible")
        return object()

    def locator(self, _selector):
        return FakeLocator(self.context)

    async def wait_for_timeout(self, milliseconds):
        self.qr_login_waited = True
        self.context.clock.advance(milliseconds / 1000)
        self.context.wait_count += 1
        if self.context.qr_mode:
            if self.context.wait_count >= self.context.login_cookie_after:
                self.context.cookies_value = list(self.qr_cookies)
            elif self.context.wait_count >= self.context.guest_cookie_after:
                self.context.cookies_value = list(self.context.guest_cookies)

    async def close(self):
        self.context.closed_pages += 1


class FakeLocator:
    def __init__(self, context):
        self.context = context

    async def click(self):
        self.context.login_button_clicks += 1
        if self.context.qr_visible_after_login_click:
            self.context.qr_visible = True


class FakeClock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class FakeContext:
    def __init__(self, qr_cookies, clock):
        self.cookies_value = []
        self.qr_cookies = qr_cookies
        self.clock = clock
        self.wait_count = 0
        self.mnsv2_ready_after = 0
        self.b1_ready_after = 0
        self.guest_cookie_after = 1
        self.login_cookie_after = 2
        self.security_verification_from = 100
        self.security_verification_until = 100
        self.qr_mode = False
        self.qr_visible = True
        self.qr_visible_after_login_click = False
        self.login_button_clicks = 0
        from app.services.xhs_auth import PlaywrightTimeoutError
        self.playwright_timeout = PlaywrightTimeoutError
        self.guest_cookies = [
            {"name": "a1", "value": "guest-cookie", "domain": ".xiaohongshu.com", "path": "/"},
            {"name": "web_session", "value": "guest-session", "domain": ".xiaohongshu.com", "path": "/"},
        ]
        self.added_cookies = []
        self.cookies_calls = 0
        self.page = FakePage(self, qr_cookies)
        self.pages = [self.page]
        self.new_page_calls = 0
        self.closed_pages = 0
        self.closed = 0

    async def add_cookies(self, cookies):
        self.added_cookies.extend(cookies)
        self.cookies_value.extend(cookies)

    async def new_page(self):
        self.new_page_calls += 1
        return self.page

    async def cookies(self):
        self.cookies_calls += 1
        return list(self.cookies_value)

    async def close(self):
        self.closed += 1


class FakeBrowser:
    def __init__(self, context):
        self.context = context
        self.closed = 0

    async def new_context(self, **_kwargs):
        return self.context

    async def close(self):
        self.closed += 1


class FakePlaywright:
    def __init__(self, context):
        self.chromium = type(
            "Chromium",
            (),
            {
                "launch": self.launch,
                "launch_persistent_context": self.launch_persistent_context,
            },
        )()
        self.context = context
        self.closed = 0
        self.launch_kwargs = None

    async def launch(self, **kwargs):
        self.context.qr_mode = False
        self.launch_kwargs = kwargs
        return FakeBrowser(self.context)

    async def launch_persistent_context(self, _user_data_dir, **kwargs):
        self.context.qr_mode = True
        self.context.cookies_value = list(self.context.guest_cookies)
        self.launch_kwargs = kwargs
        return self.context

    async def stop(self):
        self.closed += 1


class XhsAuthTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.session_path = Path("xhs-auth-test-session.json")
        self.addCleanup(lambda: self.session_path.unlink(missing_ok=True))
        self.qr_cookies = [
            {"name": "a1", "value": "qr-cookie", "domain": ".xiaohongshu.com", "path": "/"},
            {"name": "web_session", "value": "logged-in", "domain": ".xiaohongshu.com", "path": "/"},
        ]
        self.clock = FakeClock()
        self.context = FakeContext(self.qr_cookies, self.clock)
        self.fake_page = self.context.page
        self.fake_playwright = FakePlaywright(self.context)

        async def factory():
            return self.fake_playwright

        self.playwright_factory = factory

    def _service(self, options, *, manual_confirmation_requested=lambda: False):
        return XhsAuthService(
            options,
            session_file=self.session_path,
            playwright_factory=self.playwright_factory,
            is_interactive=lambda: True,
            monotonic=self.clock,
            manual_confirmation_requested=manual_confirmation_requested,
        )

    def test_xhs_settings_defaults_are_exposed(self):
        self.assertEqual("", settings.xhs_cookie)
        self.assertEqual("", settings.xhs_after_date)
        self.assertEqual(20, settings.xhs_comment_limit)
        self.assertEqual(1.0, settings.xhs_request_interval)
        self.assertEqual(".xhs_session.json", settings.xhs_session_file)

    def test_parse_cookie_string_ignores_malformed_segments(self):
        self.assertEqual(
            {"a1": "one", "web_session": "two=three"},
            parse_cookie_string("a1=one; bad; web_session=two=three"),
        )

    async def test_force_login_ignores_explicit_cookie_and_saved_session(self):
        self.session_path.write_text(json.dumps({"cookies": [{"name": "a1", "value": "saved"}]}))
        service = self._service(XhsAuthOptions(force_login=True, cookie="a1=explicit"))

        session = await service.open()

        self.assertEqual("qr-cookie", session.cookie_dict["a1"])
        self.assertTrue(self.fake_page.qr_login_waited)
        self.assertFalse(self.fake_playwright.launch_kwargs["headless"])
        self.assertEqual(0, self.context.new_page_calls)
        self.assertFalse(any(cookie["value"] == "explicit" for cookie in self.context.added_cookies))
        await service.close()

    async def test_explicit_cookie_beats_environment_and_saved_session(self):
        self.session_path.write_text(json.dumps({"cookies": self.qr_cookies}))
        service = self._service(
            XhsAuthOptions(
                cookie="a1=explicit; web_session=explicit-session",
                env_cookie="a1=environment; web_session=environment-session",
            )
        )

        session = await service.open()

        self.assertEqual("explicit", session.cookie_dict["a1"])
        await service.close()

    async def test_environment_cookie_beats_saved_session(self):
        self.session_path.write_text(json.dumps({"cookies": self.qr_cookies}))
        service = self._service(
            XhsAuthOptions(env_cookie="a1=environment; web_session=environment-session")
        )

        session = await service.open()

        self.assertEqual("environment", session.cookie_dict["a1"])
        await service.close()

    async def test_saved_session_is_loaded(self):
        self.session_path.write_text(json.dumps({"cookies": self.qr_cookies}))
        service = self._service(XhsAuthOptions())

        session = await service.open()

        self.assertEqual("qr-cookie", session.cookie_dict["a1"])
        await service.close()

    async def test_incomplete_saved_session_starts_qr_login(self):
        self.session_path.write_text(json.dumps({"cookies": [{"name": "a1", "value": "stale"}]}))
        service = self._service(XhsAuthOptions(login_timeout=1))

        session = await service.open()

        self.assertEqual("qr-cookie", session.cookie_dict["a1"])
        self.assertTrue(self.fake_page.qr_login_waited)
        self.assertFalse(self.fake_playwright.launch_kwargs["headless"])
        await service.close()

    async def test_non_interactive_without_session_fails(self):
        service = XhsAuthService(
            XhsAuthOptions(interactive=False),
            session_file=self.session_path,
            playwright_factory=self.playwright_factory,
            is_interactive=lambda: False,
        )

        with self.assertRaises(XhsAuthenticationError):
            await service.open()

    async def test_invalid_cookie_fails_authenticated_state_verification(self):
        service = self._service(XhsAuthOptions(cookie="a1=only", login_timeout=0.5))

        with self.assertRaisesRegex(XhsAuthenticationError, "missing: web_session cookie"):
            await service.open()

    async def test_interactive_without_session_enters_qr_and_persists_securely(self):
        service = self._service(XhsAuthOptions(login_timeout=1))

        session = await service.open()

        self.assertEqual("qr-cookie", session.cookie_dict["a1"])
        self.assertTrue(self.session_path.exists())
        self.assertEqual(0o600, os.stat(self.session_path).st_mode & 0o777)
        self.assertEqual("qr-cookie", json.loads(self.session_path.read_text())["cookies"][0]["value"])
        await service.close()

    async def test_missing_signing_function_fails(self):
        original_evaluate = self.fake_page.evaluate

        async def no_signing(expression):
            if "mnsv2" in expression:
                return False
            return await original_evaluate(expression)

        self.fake_page.evaluate = no_signing
        service = self._service(
            XhsAuthOptions(cookie="a1=one; web_session=two", login_timeout=0.5)
        )

        with self.assertRaisesRegex(XhsAuthenticationError, "missing: window.mnsv2"):
            await service.open()

    async def test_missing_b1_signing_state_fails(self):
        original_evaluate = self.fake_page.evaluate

        async def no_b1(expression):
            if "localStorage" in expression:
                return ""
            return await original_evaluate(expression)

        self.fake_page.evaluate = no_b1
        service = self._service(
            XhsAuthOptions(cookie="a1=one; web_session=two", login_timeout=0.5)
        )

        with self.assertRaisesRegex(XhsAuthenticationError, "missing: b1 localStorage"):
            await service.open()

    async def test_qr_login_waits_for_delayed_b1(self):
        self.context.b1_ready_after = 2
        service = self._service(XhsAuthOptions(login_timeout=1))

        session = await service.open()

        self.assertEqual("qr-cookie", session.cookie_dict["a1"])
        self.assertEqual(2, self.context.wait_count)
        await service.close()

    async def test_qr_login_captures_web_session_before_readiness_polling(self):
        self.context.cookies_value = list(self.context.guest_cookies)
        self.context.b1_ready_after = 1
        self.context.login_cookie_after = 1
        service = self._service(XhsAuthOptions(login_timeout=1))

        session = await service.open()

        self.assertEqual("logged-in", session.cookie_dict["web_session"])
        self.assertEqual(1, self.context.wait_count)
        await service.close()

    async def test_qr_login_button_fallback_exposes_qr_code(self):
        self.context.qr_visible = False
        self.context.qr_visible_after_login_click = True
        service = self._service(XhsAuthOptions(login_timeout=1))

        session = await service.open()

        self.assertEqual("logged-in", session.cookie_dict["web_session"])
        self.assertEqual(1, self.context.login_button_clicks)
        await service.close()

    async def test_missing_qr_login_ui_fails_with_actionable_error(self):
        self.context.qr_visible = False
        service = self._service(XhsAuthOptions(login_timeout=1))

        with self.assertRaisesRegex(
            XhsAuthenticationError,
            "QR-code login UI did not appear",
        ):
            await service.open()

        self.assertEqual(1, self.context.login_button_clicks)
        self.assertFalse(self.session_path.exists())

    async def test_qr_guest_signing_state_does_not_count_as_authenticated(self):
        self.context.login_cookie_after = 100
        service = self._service(XhsAuthOptions(login_timeout=0.5))

        with self.assertRaisesRegex(
            XhsAuthenticationError,
            "QR-code login was not confirmed",
        ):
            await service.open()

        self.assertFalse(self.session_path.exists())

    async def test_qr_login_waits_for_web_session_change(self):
        self.context.login_cookie_after = 3
        service = self._service(XhsAuthOptions(login_timeout=2))

        session = await service.open()

        self.assertEqual("qr-cookie", session.cookie_dict["a1"])
        self.assertEqual(3, self.context.wait_count)
        await service.close()

    async def test_qr_login_waits_for_second_security_verification(self):
        self.context.login_cookie_after = 2
        self.context.security_verification_from = 2
        self.context.security_verification_until = 4
        service = self._service(XhsAuthOptions(login_timeout=2))

        session = await service.open()

        self.assertEqual("qr-cookie", session.cookie_dict["a1"])
        self.assertEqual(4, self.context.wait_count)
        await service.close()

    async def test_enter_requests_immediate_identity_recheck(self):
        confirmations = iter((False, True, False))
        self.context.login_cookie_after = 2
        service = self._service(
            XhsAuthOptions(login_timeout=1),
            manual_confirmation_requested=lambda: next(confirmations, False),
        )

        session = await service.open()

        self.assertLess(self.clock.value, 1.0)
        await service.close()

    async def test_enter_does_not_bypass_failed_identity_check(self):
        confirmations = iter((False, True, False, False, False, False))
        self.context.login_cookie_after = 100
        service = self._service(
            XhsAuthOptions(login_timeout=0.5),
            manual_confirmation_requested=lambda: next(confirmations, False),
        )

        with self.assertRaisesRegex(
            XhsAuthenticationError,
            "QR-code login was not confirmed",
        ):
            await service.open()

        self.assertFalse(self.session_path.exists())

    async def test_cookie_mode_waits_for_delayed_signing_function(self):
        self.context.mnsv2_ready_after = 2
        service = self._service(
            XhsAuthOptions(
                cookie="a1=one; web_session=two",
                login_timeout=1,
            )
        )

        session = await service.open()

        self.assertEqual("one", session.cookie_dict["a1"])
        self.assertEqual(2, self.context.wait_count)
        await service.close()

    async def test_successful_readiness_snapshot_avoids_cookie_reread(self):
        service = self._service(XhsAuthOptions(cookie="a1=one; web_session=two"))

        session = await service.open()

        self.assertEqual("one", session.cookie_dict["a1"])
        self.assertEqual(1, self.context.cookies_calls)
        await service.close()

    async def test_timeout_lists_only_missing_fields_in_stable_order(self):
        self.context.mnsv2_ready_after = 10
        self.context.b1_ready_after = 10
        service = self._service(
            XhsAuthOptions(
                cookie="a1=one; web_session=two",
                login_timeout=0.5,
            )
        )

        with self.assertRaisesRegex(
            XhsAuthenticationError,
            r"missing: window\.mnsv2, b1 localStorage\.",
        ):
            await service.open()

    async def test_timeout_lists_all_missing_fields_and_does_not_persist(self):
        self.context.qr_cookies = []
        self.fake_page.qr_cookies = []
        self.context.guest_cookies = []
        self.context.mnsv2_ready_after = 10
        self.context.b1_ready_after = 10
        service = self._service(XhsAuthOptions(login_timeout=0.5))

        with self.assertRaisesRegex(
            XhsAuthenticationError,
            r"missing: web_session cookie, a1 cookie, window\.mnsv2, b1 localStorage\.",
        ):
            await service.open()

        self.assertFalse(self.session_path.exists())

    async def test_close_is_idempotent_and_context_manager_returns_session(self):
        service = self._service(XhsAuthOptions(cookie="a1=one; web_session=two"))

        async with service as session:
            self.assertEqual("one", session.cookie_dict["a1"])
        await service.close()

        self.assertEqual(1, self.context.closed)


if __name__ == "__main__":
    unittest.main()
