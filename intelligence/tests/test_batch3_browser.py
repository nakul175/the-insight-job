"""Real local Django UI journey, synthetic records only."""

import hashlib
import json
import os

from asgiref.sync import async_to_sync
from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client
from django.urls import reverse

from intelligence.content import materialize
from intelligence.models import Capture, WorkItem

from . import test_batch1 as fixtures
from .test_batch3 import field


class ContentBrowserTests(StaticLiveServerTestCase):
    def test_real_browser_search_schema_preview_and_filtered_return(self):
        if os.environ.get("TIJ_BROWSER_TESTS") != "true":
            self.skipTest("Requires explicit local/CI synthetic browser profile.")
        fixtures.BatchOneTests.setUpTestData.__func__(self.__class__)
        item = WorkItem.objects.create(
            workspace=self.workspace,
            source=self.source,
            source_version=self.version,
            actor=self.admin,
        )
        data = b"Tender fixture\nBudget: 12 USD"
        capture = Capture.objects.create(
            workspace=self.workspace,
            source=self.source,
            run=item,
            url=self.source.url,
            digest=hashlib.sha256(data).hexdigest(),
            media_type="text/plain",
            original=data,
            status="captured",
        )
        record, _, _ = materialize(
            capture,
            {
                "status": "parsed",
                "segments": [{"kind": "text", "section": 1, "text": data.decode()}],
                "reason": "",
            },
        )
        client = Client()
        client.force_login(
            self.admin, backend="django.contrib.auth.backends.ModelBackend"
        )
        cookie = client.cookies[settings.SESSION_COOKIE_NAME].value
        async_to_sync(self.journey)(record, cookie)

    async def journey(self, record, cookie):
        from playwright.async_api import async_playwright, expect

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
                    + reverse("records", args=[self.workspace.pk])
                    + "?q=Tender&sort=title"
                )
                await page.get_by_role(
                    "link", name="Tender fixture", exact=True
                ).click()
                await expect(
                    page.get_by_text("Geography Unknown", exact=False)
                ).to_be_visible()
                await page.get_by_role(
                    "link", name="Back to filtered results and position"
                ).click()
                await page.wait_for_url(
                    lambda url: "q=Tender" in url and "sort=title" in url
                )
                self.assertIn("q=Tender", page.url)
                self.assertIn("sort=title", page.url)
                await page.locator("summary").filter(has_text="Configure").click()
                await page.get_by_role("link", name="Schemas", exact=True).click()
                await page.get_by_role("link", name="Create a draft").click()
                await page.locator("#id_name").fill("Tender schema")
                await page.locator("#id_key").fill("tender")
                await page.locator("#id_fields").fill(json.dumps([field(unit="USD")]))
                await page.get_by_role("button", name="Save draft", exact=True).click()
                await expect(
                    page.get_by_role("heading", name="Tender schema", exact=True)
                ).to_be_visible()
                await page.goto(
                    self.live_server_url
                    + reverse("record-detail", args=[self.workspace.pk, record.pk])
                )
                await page.get_by_label("Schema version", exact=True).select_option(
                    index=0
                )
                await page.get_by_role(
                    "button", name="Run explicit preview", exact=True
                ).click()
                await page.get_by_role("link", name="budget", exact=True).wait_for()
                self.assertIn("budget", await page.locator("body").inner_text())
                await page.get_by_role("link", name="budget", exact=True).click()
                await expect(
                    page.get_by_text("Original candidate 12 USD", exact=False)
                ).to_be_visible()
                await expect(
                    page.get_by_text("exact_label", exact=False)
                ).to_be_visible()
                # A small viewport must retain all controls without horizontal overflow.
                self.assertLessEqual(
                    await page.evaluate("document.documentElement.scrollWidth"), 390
                )
            finally:
                await browser.close()
