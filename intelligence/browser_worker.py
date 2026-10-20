import asyncio
import base64
import os
from urllib.parse import urlsplit

from asgiref.sync import async_to_sync, sync_to_async
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import Http404

from .access import handoff_permission, verify_identity
from .egress_proxy import DomainProxy
from .models import BrowserAction, BrowserHandoff, Connection
from .vault import seal, unseal


def checked_handoff(handoff_id):
    handoff = BrowserHandoff.objects.select_related(
        "owner", "connection", "source"
    ).get(pk=handoff_id)
    handoff_permission(handoff.owner, handoff)
    return handoff


def next_action(handoff_id):
    return (
        BrowserAction.objects.filter(handoff_id=handoff_id)
        .order_by("created_at")
        .first()
    )


def write_handoff(handoff_id, **values):
    return BrowserHandoff.objects.filter(
        pk=handoff_id,
        state__in=["pending", "active"],
        connection__current_handoff_id=handoff_id,
    ).update(**values)


@transaction.atomic
def finish_handoff(handoff_id):
    handoff = BrowserHandoff.objects.get(pk=handoff_id)
    connection = Connection.objects.select_for_update().get(pk=handoff.connection_id)
    BrowserAction.objects.filter(handoff_id=handoff_id).delete()
    BrowserHandoff.objects.filter(
        pk=handoff_id, state__in=["pending", "active"]
    ).update(state="failed", frame_ciphertext="", reason="Browser ended; reconnect.")
    if (
        connection.current_handoff_id == handoff_id
        and handoff.state != "verified"
        and connection.state == "connecting"
    ):
        connection.state = "disconnected"
        connection.session_ciphertext = ""
        connection.save(update_fields=["state", "session_ciphertext"])


async def perform_action(page, connection, kind, payload):
    if kind == "click":
        await page.mouse.click(int(payload["x"]), int(payload["y"]))
    elif kind == "type":
        await page.keyboard.insert_text(payload["text"])
    elif kind == "key":
        await page.keyboard.press(payload["key"])
    elif kind == "scroll":
        await page.mouse.wheel(0, max(-800, min(800, int(payload.get("y", 400)))))
    elif kind == "verify":
        if urlsplit(page.url).hostname != connection.domain:
            raise ValidationError(
                "Return to the approved source domain before verification."
            )
        locator = page.locator(connection.identity_selector)
        if await locator.count() != 1:
            raise ValidationError(
                "The configured account-identity element is not uniquely present."
            )
        identity = (await locator.inner_text()).strip()
        state = await page.context.storage_state()
        await sync_to_async(verify_identity, thread_sensitive=True)(
            connection, identity, state
        )
        return (
            "identity_changed" if connection.state == "identity_changed" else "verified"
        )
    elif kind == "cancel":
        return "cancelled"
    return "active"


async def process_handoff_async(handoff_id):
    from playwright.async_api import Error as PlaywrightError
    from playwright.async_api import async_playwright

    handoff = await sync_to_async(checked_handoff, thread_sensitive=True)(handoff_id)
    connection = handoff.connection
    options = {
        "headless": True,
        "chromium_sandbox": True,
        "env": {
            key: os.environ[key]
            for key in ["PATH", "HOME", "LANG", "DISPLAY", "XDG_RUNTIME_DIR", "TMPDIR"]
            if key in os.environ
        },
        "args": [
            "--disable-quic",
            "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
            "--proxy-bypass-list=<-loopback>",
        ],
    }
    if os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE"):
        options["executable_path"] = os.environ["PLAYWRIGHT_CHROMIUM_EXECUTABLE"]
    try:
        with DomainProxy([connection.domain]) as proxy:
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(
                    proxy={"server": proxy}, **options
                )
                try:
                    context = await browser.new_context(
                        viewport={"width": 1280, "height": 800},
                        accept_downloads=False,
                        service_workers="block",
                    )
                    context.set_default_timeout(10000)
                    page = await context.new_page()
                    await page.goto(
                        connection.login_url,
                        wait_until="domcontentloaded",
                        timeout=20000,
                    )
                    activated = await sync_to_async(
                        write_handoff, thread_sensitive=True
                    )(handoff_id, state="active")
                    if not activated:
                        return
                    while True:
                        handoff = await sync_to_async(
                            BrowserHandoff.objects.get, thread_sensitive=True
                        )(pk=handoff_id)
                        if handoff.state != "active":
                            break
                        handoff = await sync_to_async(
                            checked_handoff, thread_sensitive=True
                        )(handoff_id)
                        action = await sync_to_async(
                            next_action, thread_sensitive=True
                        )(handoff_id)
                        if action:
                            try:
                                state = await perform_action(
                                    page,
                                    handoff.connection,
                                    action.kind,
                                    unseal(action.payload_ciphertext),
                                )
                            except (ValueError, TypeError, KeyError):
                                # A malformed persisted action affects this handoff only.
                                state = "failed"
                            finally:
                                await sync_to_async(
                                    action.delete, thread_sensitive=True
                                )()
                            if state != "active":
                                await sync_to_async(
                                    write_handoff, thread_sensitive=True
                                )(handoff_id, state=state, frame_ciphertext="")
                                break
                        frame = await page.screenshot(type="jpeg", quality=60)
                        await sync_to_async(write_handoff, thread_sensitive=True)(
                            handoff_id,
                            frame_ciphertext=seal(
                                {"frame": base64.b64encode(frame).decode()}
                            ),
                        )
                        await asyncio.sleep(0.5)
                finally:
                    await browser.close()
    except (ValidationError, PermissionDenied, Http404, PlaywrightError, OSError):
        await sync_to_async(write_handoff, thread_sensitive=True)(
            handoff_id,
            state="failed",
            reason="Login ended or the approved browser/domain/identity requirements could not be met.",
            frame_ciphertext="",
        )
    finally:
        await sync_to_async(finish_handoff, thread_sensitive=True)(handoff_id)


def process_handoff(handoff):
    return async_to_sync(process_handoff_async)(handoff.pk)
