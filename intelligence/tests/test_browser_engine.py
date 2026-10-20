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
