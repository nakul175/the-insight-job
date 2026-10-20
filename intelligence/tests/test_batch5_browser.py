"""Synthetic review claims/corrections through the real narrow-screen UI."""

import os

from asgiref.sync import async_to_sync
from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client
from django.urls import reverse

from intelligence.routing import route_one

from . import test_batch1 as fixtures
from . import test_batch5 as workflow_fixtures


class ReviewBrowserTests(StaticLiveServerTestCase):
    pipeline = workflow_fixtures.BatchFiveTests.pipeline

    def test_claim_correct_and_inspect_pinned_review_history(self):
        if os.environ.get("TIJ_BROWSER_TESTS") != "true":
            self.skipTest("Requires synthetic browser profile.")
        fixtures.BatchOneTests.setUpTestData.__func__(self.__class__)
        record, _revision, system = self.pipeline(text="Tender\nBudget: 2")
        result, _created = route_one(
            self.admin, self.workspace.pk, record.pk, system.pk
        )
        client = Client()
        client.force_login(
            self.reviewer, backend="django.contrib.auth.backends.ModelBackend"
        )
        async_to_sync(self.journey)(
            result, client.cookies[settings.SESSION_COOKIE_NAME].value
        )

    async def journey(self, result, cookie):
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
                    self.live_server_url
                    + reverse("review-queue", args=[self.workspace.pk])
                )
                await page.locator(
                    f'a[href="{reverse("review-detail", args=[self.workspace.pk, result.pk])}"]'
                ).click()
                self.assertIn(
                    "Pinned evidence passages", await page.locator("body").inner_text()
                )
                await page.locator("#id_action").select_option("claim")
                await page.get_by_role("button", name="Submit review action").click()
                self.assertIn(
                    "Assigned reviewer", await page.locator("body").inner_text()
                )
                await page.locator("#id_action").select_option("correct")
                await page.locator("#id_key").fill("budget")
                await page.locator("#id_value").fill("20")
                await page.locator("#id_segment").fill("0")
                await page.locator("#id_reason").fill(
                    "Synthetic correction supported by pinned evidence"
                )
                await page.get_by_role("button", name="Submit review action").click()
                text = await page.locator("body").inner_text()
                self.assertIn("pass · Grouped deterministic evaluation", text)
                self.assertIn("Synthetic correction supported by pinned evidence", text)
                self.assertIn("human_correction", text)
                self.assertIn("Preview · source version", text)
                self.assertLessEqual(
                    await page.evaluate("document.documentElement.scrollWidth"), 390
                )
            finally:
                await browser.close()
