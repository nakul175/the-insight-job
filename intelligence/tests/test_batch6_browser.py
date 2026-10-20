"""Real UI exercises local outbox delivery and immutable review completion."""

import json
import os

from asgiref.sync import async_to_sync
from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client
from django.urls import reverse

from intelligence.models import DeliveryIntent, ReviewChange, SandboxMessage
from intelligence.routing import claim_review

from . import test_batch1 as fixtures
from . import test_batch6 as delivery_fixtures


class DeliveryBrowserTests(StaticLiveServerTestCase):
    pipeline = delivery_fixtures.BatchSixTests.pipeline
    published = delivery_fixtures.BatchSixTests.published

    def test_local_delivery_and_resolved_review_via_ui(self):
        if os.environ.get("TIJ_BROWSER_TESTS") != "true":
            self.skipTest("Requires synthetic browser profile.")
        fixtures.BatchOneTests.setUpTestData.__func__(self.__class__)
        *_prefix, result = self.published()
        client = Client()
        client.force_login(
            self.admin, backend="django.contrib.auth.backends.ModelBackend"
        )
        async_to_sync(self.journey)(
            result, client.cookies[settings.SESSION_COOKIE_NAME].value
        )
        self.assertEqual(DeliveryIntent.objects.get().state, "delivered_sandbox")
        self.assertEqual(SandboxMessage.objects.get().payload, {"budget": 12})
        result = claim_review(self.admin, self.workspace.pk, result.pk, result.version)
        async_to_sync(self.complete)(
            result, client.cookies[settings.SESSION_COOKIE_NAME].value
        )
        self.assertEqual(ReviewChange.objects.filter(kind="completed").count(), 1)

    async def browser_context(self, pw, cookie):
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
        browser = await pw.chromium.launch(**options)
        context = await browser.new_context(viewport={"width": 390, "height": 844})
        await context.add_cookies(
            [
                {
                    "name": settings.SESSION_COOKIE_NAME,
                    "value": cookie,
                    "url": self.live_server_url,
                }
            ]
        )
        return browser, await context.new_page()

    async def journey(self, result, cookie):
        from playwright.async_api import async_playwright

        async with async_playwright() as pw:
            browser, page = await self.browser_context(pw, cookie)
            try:
                await page.goto(
                    self.live_server_url
                    + reverse(
                        "system-release", args=[self.workspace.pk, result.system_id]
                    )
                )
                await page.locator("#id_action").select_option("store")
                await page.locator("#id_payload").fill(
                    json.dumps(
                        {
                            "result_id": str(result.pk),
                            "expected_version": result.version,
                        }
                    )
                )
                await page.get_by_role("button", name="Apply owner action").click()
                self.assertIn("pending", await page.locator("body").inner_text())
                # Read the visible intent UUID, then use it through the documented action form.
                text = await page.locator("body").inner_text()
                intent_id = text.split("Intent ")[-1].split(" ·")[0]
                await page.locator("#id_action").select_option("deliver")
                await page.locator("#id_payload").fill(
                    json.dumps({"intent_id": intent_id})
                )
                await page.get_by_role("button", name="Apply owner action").click()
                self.assertIn(
                    "delivered_sandbox", await page.locator("body").inner_text()
                )
            finally:
                await browser.close()

    async def complete(self, result, cookie):
        from playwright.async_api import async_playwright

        async with async_playwright() as pw:
            browser, page = await self.browser_context(pw, cookie)
            try:
                await page.goto(
                    self.live_server_url
                    + reverse("review-followup", args=[self.workspace.pk, result.pk])
                )
                await page.locator("#id_action").select_option("complete")
                await page.locator("#id_reason").fill(
                    "Reviewed synthetic local delivery"
                )
                await page.get_by_role("button", name="Save review follow-up").click()
                self.assertIn("resolved", await page.locator("body").inner_text())
                await page.goto(
                    self.live_server_url
                    + reverse("review-followup", args=[self.workspace.pk, result.pk])
                )
                await page.locator("#id_action").select_option("complete")
                await page.locator("#id_reason").fill("Duplicate must be rejected")
                await page.get_by_role("button", name="Save review follow-up").click()
                self.assertIn(
                    "compare the current revision",
                    await page.locator("body").inner_text(),
                )
            finally:
                await browser.close()
