"""Narrow-screen review tabs preserve one correction form and keyboard focus."""

import os

from asgiref.sync import async_to_sync
from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client
from django.urls import reverse

from intelligence.routing import claim_review

from . import test_batch1 as fixtures
from . import test_batch6 as delivery_fixtures
from . import test_batch6_browser as browser_fixtures


class ReviewTabsBrowserTests(StaticLiveServerTestCase):
    pipeline = delivery_fixtures.BatchSixTests.pipeline
    published = delivery_fixtures.BatchSixTests.published
    browser_context = browser_fixtures.DeliveryBrowserTests.browser_context

    def test_tabs_keep_pending_value_reason_and_keyboard_selection(self):
        if os.environ.get("TIJ_BROWSER_TESTS") != "true":
            self.skipTest("Requires synthetic browser profile.")
        fixtures.BatchOneTests.setUpTestData.__func__(self.__class__)
        *_prefix, result = self.published()
        result = claim_review(self.admin, self.workspace.pk, result.pk, result.version)
        client = Client()
        client.force_login(
            self.admin, backend="django.contrib.auth.backends.ModelBackend"
        )
        async_to_sync(self.journey)(
            result, client.cookies[settings.SESSION_COOKIE_NAME].value
        )

    async def journey(self, result, cookie):
        from playwright.async_api import async_playwright

        async with async_playwright() as pw:
            browser, page = await self.browser_context(pw, cookie)
            page.on("dialog", lambda dialog: dialog.accept())
            try:
                await page.set_viewport_size({"width": 360, "height": 800})
                await page.goto(
                    self.live_server_url
                    + reverse("review-detail", args=[self.workspace.pk, result.pk])
                )
                await page.locator("#id_action").select_option("correct")
                await page.locator("#id_key").fill("budget")
                await page.locator("#id_value").fill("15")
                await page.locator("#id_segment").fill("0")
                await page.locator("#id_reason").fill(
                    "Retain my review proposal across panels"
                )
                await page.get_by_role("tab", name="Evidence", exact=True).focus()
                await page.keyboard.press("ArrowRight")
                self.assertEqual(
                    await page.get_by_role(
                        "tab", name="Decision", exact=True
                    ).get_attribute("aria-selected"),
                    "true",
                )
                self.assertTrue(
                    await page.locator("#decision-tab").evaluate(
                        "node => node === document.activeElement"
                    )
                )
                self.assertTrue(await page.locator("#evidence-panel").is_hidden())
                await page.keyboard.press("Home")
                self.assertEqual(await page.locator("#id_value").input_value(), "15")
                self.assertEqual(
                    await page.locator("#id_reason").input_value(),
                    "Retain my review proposal across panels",
                )
                self.assertEqual(await page.locator("#review-action").count(), 1)
                await page.locator("#id_expected_version").evaluate(
                    "node => node.value = '1'"
                )
                await page.get_by_role("button", name="Submit review action").click()
                await page.locator("#use-current-revision").wait_for()
                self.assertEqual(await page.locator("#id_value").input_value(), "15")
                await page.locator("#use-current-revision").click()
                self.assertEqual(
                    await page.locator("#id_expected_version").input_value(),
                    str(result.version),
                )
                self.assertEqual(
                    await page.locator("#id_reason").input_value(),
                    "Retain my review proposal across panels",
                )
                self.assertTrue(
                    await page.evaluate(
                        "document.documentElement.scrollWidth <= window.innerWidth"
                    )
                )
                await page.get_by_role("button", name="Submit review action").click()
                await page.get_by_text("human_correction", exact=False).first.wait_for()
            finally:
                await browser.close()
