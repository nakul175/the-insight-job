"""Trusted local HTML fixture only; never signs into a real source account."""

import os
from unittest.mock import patch

from asgiref.sync import async_to_sync
from cryptography.fernet import Fernet
from django.test import TestCase

from intelligence.browser_worker import perform_action
from intelligence.models import Connection

from . import test_batch1 as fixtures


class BrowserEngineTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        fixtures.BatchOneTests.setUpTestData.__func__(cls)

    async def exercise_browser(self, conn):
        from playwright.async_api import async_playwright

        options = {
            "headless": True,
            "args": ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
            "env": {
                k: os.environ[k]
                for k in ["PATH", "HOME", "LD_LIBRARY_PATH", "LANG"]
                if k in os.environ
            },
        }
        if os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE"):
            options["executable_path"] = os.environ["PLAYWRIGHT_CHROMIUM_EXECUTABLE"]
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(**options)
            try:
                first = await browser.new_context()
                second = await browser.new_context()

                async def fixture_route(route):
                    await route.fulfill(
                        status=200,
                        content_type="text/html",
                        body='<h1 id="account">Analyst</h1>',
                    )

                await first.route("**/*", fixture_route)
                await first.add_cookies(
                    [
                        {
                            "name": "session",
                            "value": "synthetic-fixture-cookie",
                            "domain": "example.com",
                            "path": "/",
                        }
                    ]
                )
                page = await first.new_page()
                await page.goto("https://example.com/profile")
                result = await perform_action(page, conn, "verify", {})
                isolated_cookies = await second.cookies()
                return result, isolated_cookies
            finally:
                await browser.close()

    def test_real_browser_context_isolation_and_identity_verification_on_local_fixture(
        self,
    ):
        if os.environ.get("TIJ_BROWSER_TESTS") != "true":
            self.skipTest(
                "Browser engine integration requires the explicit CI/local browser fixture profile; not executed by the plain unit suite."
            )
        conn = Connection.objects.create(
            workspace=self.workspace,
            owner=self.admin,
            bound_source=self.source,
            name="Fixture",
            domain="example.com",
            method="session",
            state="connecting",
            identity_selector="#account",
            expected_identity="Analyst",
        )
        with patch.dict(
            os.environ, {"TIJ_CONNECTION_KEYS": Fernet.generate_key().decode()}
        ):
            result, isolated_cookies = async_to_sync(self.exercise_browser)(conn)
        self.assertEqual(result, "verified")
        self.assertEqual(isolated_cookies, [])
        conn.refresh_from_db()
        self.assertEqual(conn.verified_identity, "Analyst")
        self.assertNotIn("synthetic-fixture-cookie", conn.session_ciphertext)

    def test_ac0302_synthetic_password_and_mfa_actions_verify_only_after_both_steps(
        self,
    ):
        if os.environ.get("TIJ_BROWSER_TESTS") != "true":
            self.skipTest("Requires the explicit synthetic browser profile.")
        conn = Connection.objects.create(
            workspace=self.workspace,
            owner=self.admin,
            bound_source=self.source,
            name="MFA fixture",
            domain="example.com",
            method="session",
            state="connecting",
            identity_selector="#account",
            expected_identity="Analyst",
        )
        with patch.dict(
            os.environ, {"TIJ_CONNECTION_KEYS": Fernet.generate_key().decode()}
        ):
            async_to_sync(self.password_mfa_journey)(conn)
        conn.refresh_from_db()
        self.assertEqual(conn.state, "connected")
        self.assertEqual(conn.verified_identity, "Analyst")
        self.assertIsNotNone(conn.checked_at)
        self.assertNotIn("fixture-password", conn.session_ciphertext)
        self.assertNotIn("123456", conn.session_ciphertext)

    async def password_mfa_journey(self, conn):
        from django.core.exceptions import ValidationError
        from playwright.async_api import async_playwright

        options = {"headless": True}
        if os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE"):
            options["executable_path"] = os.environ["PLAYWRIGHT_CHROMIUM_EXECUTABLE"]
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(**options)
            try:
                context = await browser.new_context()

                async def fixture(route):
                    await route.fulfill(
                        content_type="text/html",
                        body="""
                        <form id="password-form"><label>Password<input id="password" type="password"></label><button>Continue</button></form>
                        <form id="mfa-form" hidden><label>Code<input id="code"></label><button>Verify code</button></form>
                        <div id="identity"></div><script>
                        document.querySelector('#password-form').onsubmit=e=>{e.preventDefault();
                          if(document.querySelector('#password').value==='fixture-password'){
                            e.target.hidden=true; document.querySelector('#mfa-form').hidden=false;}};
                        document.querySelector('#mfa-form').onsubmit=e=>{e.preventDefault();
                          if(document.querySelector('#code').value==='123456'){
                            e.target.hidden=true; document.querySelector('#identity').innerHTML='<h1 id="account">Analyst</h1>';}};
                        </script>""",
                    )

                await context.route("**/*", fixture)
                page = await context.new_page()
                await page.goto("https://example.com/login")
                with self.assertRaises(ValidationError):
                    await perform_action(page, conn, "verify", {})
                await page.locator("#password").focus()
                await perform_action(page, conn, "type", {"text": "fixture-password"})
                await perform_action(page, conn, "key", {"key": "Enter"})
                self.assertTrue(await page.locator("#mfa-form").is_visible())
                with self.assertRaises(ValidationError):
                    await perform_action(page, conn, "verify", {})
                await page.locator("#code").focus()
                await perform_action(page, conn, "type", {"text": "123456"})
                await perform_action(page, conn, "key", {"key": "Enter"})
                self.assertEqual(
                    await perform_action(page, conn, "verify", {}), "verified"
                )
                second = await browser.new_context()
                self.assertEqual(await second.cookies(), [])
            finally:
                await browser.close()
