"""Keyboard/narrow-screen guided activation and permission-scoped draft recovery."""

import os

from asgiref.sync import async_to_sync
from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client
from django.urls import reverse

from intelligence.models import SourceActivation, SourceTest
from intelligence.routing import claim_review

from . import test_batch1 as fixtures
from . import test_batch6 as delivery_fixtures
from . import test_batch6_browser as browser_fixtures


class GuidedBrowserTests(StaticLiveServerTestCase):
    pipeline = delivery_fixtures.BatchSixTests.pipeline
    published = delivery_fixtures.BatchSixTests.published
    browser_context = browser_fixtures.DeliveryBrowserTests.browser_context

    def test_guided_activation_and_recoverable_review_draft_at_360_pixels(self):
        if os.environ.get("TIJ_BROWSER_TESTS") != "true":
            self.skipTest("Requires synthetic browser profile.")
        fixtures.BatchOneTests.setUpTestData.__func__(self.__class__)
        *_prefix, result = self.published()
        result = claim_review(self.admin, self.workspace.pk, result.pk, result.version)
        SourceTest.objects.create(
            workspace=self.workspace,
            source=self.source,
            source_version=self.version,
            actor=self.admin,
            state="passed",
        )
        client = Client()
        client.force_login(
            self.admin, backend="django.contrib.auth.backends.ModelBackend"
        )
        async_to_sync(self.journey)(
            result, client.cookies[settings.SESSION_COOKIE_NAME].value
        )
        self.assertTrue(SourceActivation.objects.filter(source=self.source).exists())

    async def journey(self, result, cookie):
        from playwright.async_api import async_playwright

        async with async_playwright() as pw:
            browser, page = await self.browser_context(pw, cookie)
            page.on("dialog", lambda dialog: dialog.accept())
            try:
                await page.set_viewport_size({"width": 360, "height": 800})
                await page.goto(
                    self.live_server_url
                    + reverse("source-setup", args=[self.workspace.pk, self.source.pk])
                )
                self.assertEqual(
                    await page.locator(
                        'ol[aria-label="Source setup progress"] li'
                    ).count(),
                    4,
                )
                await page.get_by_label(
                    "I confirm the exact source, access and tested scope shown above."
                ).check()
                await page.get_by_role(
                    "button", name="Confirm scope and activate"
                ).click()
                self.assertIn(
                    "Stored activation scope", await page.locator("body").inner_text()
                )
                await page.goto(
                    self.live_server_url
                    + reverse("review-followup", args=[self.workspace.pk, result.pk])
                )
                await page.locator("#id_reason").fill(
                    "Recover this unsaved synthetic review note"
                )
                await page.get_by_text(
                    "Recoverable draft saved. Submission is still required.", exact=True
                ).wait_for()
                await page.goto(
                    self.live_server_url
                    + reverse("review-queue", args=[self.workspace.pk])
                )
                await page.goto(
                    self.live_server_url
                    + reverse("review-followup", args=[self.workspace.pk, result.pk])
                )
                await page.get_by_role(
                    "button", name="Restore your saved draft"
                ).click()
                self.assertEqual(
                    await page.locator("#id_reason").input_value(),
                    "Recover this unsaved synthetic review note",
                )
                await page.keyboard.press("Tab")
                self.assertTrue(
                    await page.evaluate("document.activeElement !== document.body")
                )
                self.assertTrue(
                    await page.evaluate(
                        "document.documentElement.scrollWidth <= innerWidth"
                    )
                )
                self.assertGreater(await page.locator('[role="status"]').count(), 0)
                self.assertIsNotNone(
                    await page.locator("#id_reason").get_attribute("id")
                )
            finally:
                await browser.close()
