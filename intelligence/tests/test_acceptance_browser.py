"""Synthetic keyboard and failed-save journeys for the R1 criterion audit."""

import os

from asgiref.sync import async_to_sync
from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client
from django.urls import reverse

from intelligence.routing import claim_review

from . import test_batch1 as base
from . import test_batch6 as delivery
from . import test_batch6_browser as browser_fixture


class AcceptanceBrowserTests(StaticLiveServerTestCase):
    pipeline = delivery.BatchSixTests.pipeline
    published = delivery.BatchSixTests.published
    browser_context = browser_fixture.DeliveryBrowserTests.browser_context

    def test_core_screens_keyboard_names_mobile_and_failed_save(self):
        if os.environ.get("TIJ_BROWSER_TESTS") != "true":
            self.skipTest("Requires the explicit synthetic browser profile.")
        base.BatchOneTests.setUpTestData.__func__(self.__class__)
        record, system, *_prefix, result = self.published()
        result = claim_review(self.admin, self.workspace.pk, result.pk, result.version)
        client = Client()
        client.force_login(
            self.admin, backend="django.contrib.auth.backends.ModelBackend"
        )
        async_to_sync(self.journey)(
            record, system, result, client.cookies[settings.SESSION_COOKIE_NAME].value
        )

    async def journey(self, record, system, result, cookie):
        from playwright.async_api import async_playwright

        routes = [
            ("overview", [self.workspace.pk]),
            ("sources", [self.workspace.pk]),
            ("source-setup", [self.workspace.pk, self.source.pk]),
            ("records", [self.workspace.pk]),
            ("record-detail", [self.workspace.pk, record.pk]),
            ("review-queue", [self.workspace.pk]),
            ("review-detail", [self.workspace.pk, result.pk]),
            ("review-followup", [self.workspace.pk, result.pk]),
            ("system-setup", [self.workspace.pk, system.pk]),
            ("schemas", [self.workspace.pk]),
            ("governance", [self.workspace.pk]),
        ]
        async with async_playwright() as pw:
            browser, page = await self.browser_context(pw, cookie)
            try:
                await page.set_viewport_size({"width": 360, "height": 800})
                for route, args in routes:
                    with self.subTest(screen=route):
                        response = await page.goto(
                            self.live_server_url + reverse(route, args=args)
                        )
                        self.assertEqual(response.status, 200)
                        # A deliberately bounded keyboard sweep covers every currently visible
                        # native control, including navigation, forms and tab controls.
                        controls = await page.evaluate("""() => {
                            const nodes = [...document.querySelectorAll('a[href],button,input,select,textarea,summary')]
                              .filter(n => n.checkVisibility() && !n.disabled && n.type !== 'hidden' && n.tabIndex >= 0);
                            return nodes.map((n,i) => {
                              n.dataset.qaKeyboard = String(i);
                              const name = n.getAttribute('aria-label') || [...(n.labels || [])].map(l=>l.innerText).join(' ') ||
                                n.innerText || n.getAttribute('title');
                              return {id:String(i),name:(name || '').trim()};
                            });
                        }""")
                        self.assertTrue(controls)
                        self.assertEqual([c for c in controls if not c["name"]], [])
                        await page.locator("body").click(position={"x": 1, "y": 1})
                        await page.evaluate("document.activeElement.blur()")
                        reached = set()
                        for _ in range(len(controls) + 3):
                            await page.keyboard.press("Tab")
                            key = await page.evaluate(
                                "document.activeElement.dataset.qaKeyboard || null"
                            )
                            if key is not None:
                                reached.add(key)
                        self.assertEqual(reached, {c["id"] for c in controls})
                        if route in {
                            "review-queue",
                            "review-detail",
                            "review-followup",
                        }:
                            self.assertTrue(
                                await page.evaluate(
                                    "document.documentElement.scrollWidth <= innerWidth"
                                )
                            )

                await page.goto(
                    self.live_server_url
                    + reverse("review-detail", args=[self.workspace.pk, result.pk])
                )
                await page.locator("#id_action").select_option("correct")
                await page.locator("#id_key").fill("budget")
                await page.locator("#id_value").fill("15")
                await page.locator("#id_segment").fill("9999")
                await page.locator("#id_reason").fill("Keep this failed-save proposal")
                await page.get_by_role("button", name="Submit review action").click()
                await page.locator("#review-action .errorlist").wait_for()
                self.assertEqual(await page.locator("#id_value").input_value(), "15")
                self.assertEqual(
                    await page.locator("#id_reason").input_value(),
                    "Keep this failed-save proposal",
                )
                self.assertTrue(
                    await page.locator("#review-action .errorlist").evaluate(
                        "n => n.getAttribute('role') === 'alert' && n === document.activeElement"
                    )
                )
                self.assertGreater(
                    await page.locator('[role="status"][aria-live="polite"]').count(), 0
                )
                self.assertNotIn(
                    "Correction saved", await page.locator("body").inner_text()
                )
            finally:
                await browser.close()
