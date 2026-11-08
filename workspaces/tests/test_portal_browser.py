"""Native browser regression, separate from the external Central Portal E2E pack."""

import os

from asgiref.sync import async_to_sync, sync_to_async
from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client, override_settings

from intelligence.tests import test_batch6_browser as browser_fixture

from . import test_portal_permissions as permission_fixture


@override_settings(APLYD_SOURCE_WRITER_ENABLED=True)
class PortalOpenSessionBrowserTests(StaticLiveServerTestCase):
    preview = permission_fixture.PortalPermissionTests.preview
    apply = permission_fixture.PortalPermissionTests.apply
    browser_context = browser_fixture.DeliveryBrowserTests.browser_context

    def test_open_page_grant_downgrade_revoke_and_unverifiable(self):
        if os.environ.get("TIJ_BROWSER_TESTS") != "true":
            self.skipTest("Requires explicit synthetic browser profile.")
        permission_fixture.PortalPermissionTests.setUp(self)
        client = Client()
        client.force_login(
            self.target, backend="django.contrib.auth.backends.ModelBackend"
        )
        async_to_sync(self.journey)(client.cookies[settings.SESSION_COOKIE_NAME].value)

    async def journey(self, cookie):
        from playwright.async_api import async_playwright, expect

        expect.set_options(timeout=8000)

        async with async_playwright() as pw:
            browser, page = await self.browser_context(pw, cookie)
            page.set_default_timeout(8000)
            try:
                url = self.live_server_url + f"/workspaces/{self.workspace.pk}/"
                await page.goto(url)
                await sync_to_async(self.apply)("administrator")
                await expect(
                    page.locator('body[data-permission-stamp$=":administrator"]')
                ).to_be_visible()
                await page.goto(
                    self.live_server_url + f"/app/{self.workspace.pk}/members/"
                )
                await expect(
                    page.get_by_role("heading", name="Invite a member", exact=True)
                ).to_be_visible()
                await sync_to_async(self.apply)("viewer")
                await page.wait_for_url(url)
                await expect(
                    page.get_by_role("heading", name="Invite a member", exact=True)
                ).to_have_count(0)
                await page.route(
                    "**/permission-status/",
                    lambda route: route.fulfill(status=503, body="unavailable"),
                )
                await expect(
                    page.get_by_text(
                        "Access cannot be verified. Reconnecting…", exact=True
                    )
                ).to_be_visible()
                await page.unroute("**/permission-status/")
                await expect(
                    page.get_by_role("heading", name="Synthetic workspace", exact=True)
                ).to_be_visible()
                await sync_to_async(self.apply)("revoke")
                await expect(
                    page.get_by_text(
                        "Workspace access changed. Sign in again to continue.",
                        exact=True,
                    )
                ).to_be_visible()
                await sync_to_async(self.apply)("viewer")
                response = await page.request.get(url, timeout=5000)
                self.assertEqual(response.status, 401)
            finally:
                await browser.close()
