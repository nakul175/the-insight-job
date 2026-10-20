"""Synthetic system setup in the real Django UI at a narrow viewport."""

import json
import os

from asgiref.sync import async_to_sync
from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client
from django.urls import reverse

from . import test_batch1 as fixtures


class SystemBrowserTests(StaticLiveServerTestCase):
    def test_system_setup_draft_gaps_duplication_and_declared_destination(self):
        if os.environ.get("TIJ_BROWSER_TESTS") != "true":
            self.skipTest("Requires synthetic browser profile.")
        fixtures.BatchOneTests.setUpTestData.__func__(self.__class__)
        client = Client()
        client.force_login(
            self.admin, backend="django.contrib.auth.backends.ModelBackend"
        )
        async_to_sync(self.journey)(client.cookies[settings.SESSION_COOKIE_NAME].value)

    async def journey(self, cookie):
        from playwright.async_api import async_playwright

        options = {
            "headless": True,
            "args": ["--no-sandbox"],
            "env": {
                key: os.environ[key]
                for key in ["PATH", "HOME", "LANG", "TMPDIR"]
                if key in os.environ
            },
        }
        if os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE"):
            options["executable_path"] = os.environ["PLAYWRIGHT_CHROMIUM_EXECUTABLE"]
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(**options)
            try:
                context = await browser.new_context(
                    viewport={"width": 390, "height": 844}
                )
                await context.add_cookies(
                    [
                        {
                            "name": settings.SESSION_COOKIE_NAME,
                            "value": cookie,
                            "url": self.live_server_url,
                        }
                    ]
                )
                page = await context.new_page()
                await page.goto(
                    self.live_server_url + reverse("systems", args=[self.workspace.pk])
                )
                await page.locator("#id_name").fill("Pilot tenders")
                await page.locator("#id_purpose").fill(
                    "Review permitted procurement notices"
                )
                await page.get_by_role(
                    "button", name="Create draft", exact=True
                ).click()
                self.assertIn(
                    "Published qualification rules",
                    await page.locator("body").inner_text(),
                )
                await page.get_by_role(
                    "link", name="Configure sources, types and relevance"
                ).click()
                await page.get_by_label("Allowed", exact=True).check()
                await page.locator("#id_unknown_type").select_option("review")
                await page.locator("#id_relevance").fill(
                    json.dumps(
                        {
                            "all": ["tender"],
                            "none": [],
                            "unknown": "review",
                            "examples": [{"text": "tender", "expected": "include"}],
                        }
                    )
                )
                await page.get_by_role("button", name="Save versioned draft").click()
                self.assertIn("Draft 1", await page.locator("body").inner_text())
                await page.get_by_role("button", name="Check activation").click()
                self.assertIn(
                    "Activation blocked", await page.locator("body").inner_text()
                )
                await page.get_by_role(
                    "button", name="Duplicate into a new draft"
                ).click()
                self.assertIn(
                    "Pilot tenders (copy)", await page.locator("body").inner_text()
                )
                await page.get_by_role(
                    "link", name="Sandbox destinations", exact=True
                ).click()
                await page.locator("#id_endpoint").fill(
                    "https://sandbox.example.com/test"
                )
                await page.get_by_role(
                    "button", name="Register draft destination"
                ).click()
                await page.get_by_role("button", name="Run declared test").click()
                self.assertIn("test_passed", await page.locator("body").inner_text())
                self.assertIn("unverified", await page.locator("body").inner_text())
                self.assertLessEqual(
                    await page.evaluate("document.documentElement.scrollWidth"), 390
                )
            finally:
                await browser.close()
