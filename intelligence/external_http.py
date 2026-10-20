"""Bounded HTTPS only; validate every DNS answer and connect to that exact IP."""

import http.client
import ipaddress
import json
import os
import re
import socket
import ssl
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from urllib.parse import urlsplit

from django.core.exceptions import ValidationError
from django.db import connection

from .urlsafety import canonical_url

MAX_BYTES = 256_000
DNS_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="external-dns")


def endpoint_url(url):
    clean = canonical_url(url, resolve=False)
    p = urlsplit(clean)
    allowed = {
        x.strip().lower()
        for x in os.environ.get("TIJ_EXTERNAL_HOSTS", "").split(",")
        if x.strip()
    }
    if (
        p.scheme != "https"
        or p.port not in {None, 443}
        or p.query
        or urlsplit(url).fragment
        or p.hostname not in allowed
    ):
        raise ValidationError(
            "Choose an operator-approved HTTPS host on port443 without query/fragment."
        )
    return clean


def request(destination, operation, lookup=False):
    if connection.in_atomic_block:
        raise ValidationError("External HTTP must run outside database transactions.")
    endpoint = endpoint_url(destination.endpoint)
    if not re.fullmatch(r"TIJ_SECRET_[A-Z0-9_]{1,100}", destination.secret_ref):
        raise ValidationError("Use a protected TIJ_SECRET_ authentication reference.")
    token = os.environ.get(destination.secret_ref, "")
    if (
        not token
        or len(token) > 4096
        or any(ord(c) < 33 or ord(c) > 126 for c in token)
    ):
        raise ValidationError("Destination authentication is unavailable.")
    p = urlsplit(endpoint)
    deadline = time.monotonic() + 20
    future = DNS_POOL.submit(
        socket.getaddrinfo, p.hostname, 443, type=socket.SOCK_STREAM
    )
    try:
        addresses = future.result(timeout=3)
    except FutureTimeout as exc:
        future.cancel()
        raise OSError("Destination DNS budget exhausted.") from exc
    if not addresses or any(
        not ipaddress.ip_address(x[4][0]).is_global for x in addresses
    ):
        raise ValidationError("Non-public destination resolution blocked.")
    ip = addresses[0][4][0]
    conn = http.client.HTTPSConnection(
        p.hostname, 443, timeout=5, context=ssl.create_default_context()
    )
    conn._create_connection = lambda target, timeout, source_address=None: (
        socket.create_connection((ip, 443), timeout, source_address)
    )
    path = p.path or "/"
    if lookup:
        path = path.rstrip("/") + "/receipts/" + operation.operation_key
    body = (
        None
        if lookup
        else json.dumps(
            operation.payload, sort_keys=True, separators=(",", ":")
        ).encode()
    )
    if body is not None and len(body) > MAX_BYTES:
        raise ValidationError("Evidence package exceeds256KB.")
    try:
        conn.request(
            "GET" if lookup else "POST",
            path,
            body=body,
            headers={
                "Authorization": "Bearer " + token,
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Idempotency-Key": operation.operation_key,
                "X-Payload-SHA256": operation.payload_digest,
            },
        )
        active_socket = conn.sock
        response = conn.getresponse()
        if response.status not in {200, 201}:
            raise OSError(
                "Receipt not confirmed; redirects and non-success responses are refused."
            )
        data = b""
        while len(data) <= 8192:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise OSError("Receipt time budget exhausted.")
            if active_socket:
                active_socket.settimeout(min(5, remaining))
            chunk = response.read1(min(1024, 8193 - len(data)))
            if not chunk:
                break
            data += chunk
        if len(data) > 8192:
            raise OSError("Receipt exceeds the response budget.")
        return json.loads(data)
    finally:
        conn.close()
