import hashlib
import http.client
import ipaddress
import socket
import ssl
import time
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup
from django.core.exceptions import ValidationError

from .urlsafety import canonical_url, scope_decision

MAX_BYTES = 2_000_000


def fetch_public(url, base, config, headers=None):
    skipped = []
    deadline = time.monotonic() + 30
    for _ in range(4):
        if time.monotonic() >= deadline:
            raise ValidationError("Sample time budget exhausted.")
        url = canonical_url(url)
        allowed, rule = scope_decision(base, url, config)
        if not allowed:
            return {
                "state": "blocked",
                "missing": [{"url": url, "reason": rule}],
                "bytes": b"",
                "format": "unknown",
            }
        p = urlsplit(url)
        port = p.port or (443 if p.scheme == "https" else 80)
        addresses = socket.getaddrinfo(p.hostname, port, type=socket.SOCK_STREAM)
        if not addresses or any(
            not ipaddress.ip_address(a[4][0]).is_global for a in addresses
        ):
            raise ValidationError("Private-network resolution blocked.")
        address = addresses[0][4][0]
        connection = (
            http.client.HTTPSConnection(
                p.hostname, port, timeout=10, context=ssl.create_default_context()
            )
            if p.scheme == "https"
            else http.client.HTTPConnection(p.hostname, port, timeout=10)
        )
        # Pin the validated address while retaining hostname/SNI and certificate checks.
        connection._create_connection = (
            lambda target, timeout, source_address=None, address=address, port=port: (
                socket.create_connection((address, port), timeout, source_address)
            )
        )
        try:
            connection.request(
                "GET",
                p.path + ("?" + p.query if p.query else ""),
                headers={
                    "User-Agent": "TheInsightJob/0.1",
                    "Accept-Encoding": "identity",
                    **(headers or {}),
                },
            )
            response = connection.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                location = response.getheader("Location")
                if not location:
                    raise ValidationError("Redirect has no destination.")
                target = urljoin(url, location)
                allowed, reason = scope_decision(base, target, config)
                if not allowed:
                    return {
                        "state": "partial",
                        "missing": [{"url": target, "reason": reason}],
                        "bytes": b"",
                        "format": "unknown",
                    }
                url = target
                continue
            if response.status in {401, 403, 429}:
                return {
                    "state": "needs_access",
                    "missing": [{"reason": "Access denied or rate limited"}],
                    "bytes": b"",
                    "format": "unknown",
                }
            if response.status >= 400:
                raise ValidationError(f"Source returned HTTP {response.status}.")
            if response.getheader("Content-Encoding", "identity") != "identity":
                raise ValidationError(
                    "Compressed responses are unsupported in bounded sampling."
                )
            chunks = []
            total = 0
            while total <= MAX_BYTES:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ValidationError("Sample time budget exhausted.")
                if connection.sock:
                    connection.sock.settimeout(min(10, remaining))
                chunk = response.read1(min(65536, MAX_BYTES + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
            data = b"".join(chunks)
            if len(data) > MAX_BYTES:
                raise ValidationError("Sample exceeds the 2 MB byte limit.")
            kind = (
                response.getheader("Content-Type", "application/octet-stream")
                .split(";")[0]
                .lower()
            )
            return {
                "state": "captured",
                "bytes": data,
                "format": kind,
                "url": url,
                "missing": skipped,
            }
        finally:
            connection.close()
    raise ValidationError("Sample redirect limit reached.")


def sample_report(capture):
    data = capture["bytes"]
    kind = capture["format"]
    missing = list(capture.get("missing", []))
    text = ""
    formats = [kind]
    if kind in {"text/html", "application/xhtml+xml"}:
        soup = BeautifulSoup(data, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        text = soup.get_text(" ", strip=True)
        for tag in soup.find_all(["img", "video", "audio", "iframe", "object"]):
            missing.append(
                {
                    "reason": "Embedded media is not captured by the public HTML connector",
                    "element": tag.name,
                }
            )
        for a in soup.select("a[href]"):
            if (
                a.get("href", "")
                .lower()
                .split("?")[0]
                .endswith((".pdf", ".docx", ".mp4", ".mp3"))
            ):
                missing.append(
                    {
                        "reason": "Linked attachment not included in the one-page sample",
                        "url": a["href"][:2000],
                    }
                )
    elif kind.startswith("text/"):
        text = data.decode("utf-8", errors="replace").strip()
    else:
        missing.append({"reason": "Unsupported sample format"})
    if not text:
        state = "failed"
    elif missing:
        state = "partial"
    else:
        state = "passed"
    if capture["state"] in {"blocked", "needs_access"}:
        state = capture["state"]
    return {
        "state": state,
        "formats": formats,
        "byte_count": len(data),
        "text_characters": len(text),
        "hash": hashlib.sha256(data).hexdigest(),
        "missing": missing,
        "scope": "One page, at most 2 MB, at most 3 redirects, 10 seconds per connection",
        "completeness": "partial"
        if missing
        else ("text captured" if text else "empty"),
        "downstream_delivery": False,
    }
