"""Domain-restricted, DNS-pinned CONNECT proxy for isolated login browsers.

TLS remains end to end: no interception, certificate disabling or credential logs.
"""

import select
import socket
import socketserver
import threading
import time
from urllib.parse import urlsplit

from django.core.exceptions import ValidationError

from .urlsafety import canonical_url


class ProxyServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, allowed_hosts):
        self.allowed_hosts = frozenset(allowed_hosts)
        self.deadline = time.monotonic() + 1200
        self.remaining = 20_000_000
        self.lock = threading.Lock()
        self.requests = 0
        self.slots = threading.BoundedSemaphore(8)
        super().__init__(("127.0.0.1", 0), ProxyHandler)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except (OSError, RuntimeError):
            self.slots.release()
            self.shutdown_request(request)

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def debit(self, n):
        with self.lock:
            self.remaining -= n
            if self.remaining < 0 or time.monotonic() > self.deadline:
                raise OSError("Browser network budget exhausted")


class ProxyHandler(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(10)
        try:
            line = self.rfile.readline(8193)
            if len(line) > 8192:
                return
            method, target, _ = line.decode("ascii").strip().split(" ", 2)
            if method != "CONNECT":
                return
            headers = []
            size = 0
            while True:
                line = self.rfile.readline(8193)
                size += len(line)
                if size > 32768:
                    return
                if line in {b"\r\n", b"\n", b""}:
                    break
                headers.append(line)
            if method == "CONNECT":
                p = urlsplit("https://" + target)
                if p.port not in {None, 443}:
                    return
                url = "https://" + target + "/"
            else:
                p = urlsplit(target)
                url = target
                if p.scheme != "http" or p.port not in {None, 80}:
                    return
            if p.hostname not in self.server.allowed_hosts:
                return
            canonical_url(url)
            addresses = socket.getaddrinfo(
                p.hostname,
                p.port or (443 if method == "CONNECT" else 80),
                type=socket.SOCK_STREAM,
            )
            # Revalidate all final answers immediately before pinning the connection.
            import ipaddress

            if not addresses or any(
                not ipaddress.ip_address(a[4][0]).is_global for a in addresses
            ):
                return
            with self.server.lock:
                self.server.requests += 1
                if self.server.requests > 150:
                    return
            upstream = socket.create_connection(addresses[0][4], timeout=10)
            with upstream:
                if method == "CONNECT":
                    self.connection.sendall(
                        b"HTTP/1.1 200 Connection Established\r\n\r\n"
                    )
                else:
                    path = (p.path or "/") + ("?" + p.query if p.query else "")
                    upstream.sendall(
                        (method + " " + path + " HTTP/1.1\r\n").encode()
                        + b"".join(
                            h for h in headers if not h.lower().startswith(b"proxy-")
                        )
                        + b"\r\n"
                    )
                self.connection.setblocking(False)
                upstream.setblocking(False)
                while time.monotonic() < self.server.deadline:
                    ready, _, _ = select.select([self.connection, upstream], [], [], 5)
                    if not ready:
                        continue
                    for src in ready:
                        data = src.recv(65536)
                        if not data:
                            return
                        self.server.debit(len(data))
                        dest = upstream if src is self.connection else self.connection
                        dest.settimeout(5)
                        dest.sendall(data)
                        dest.setblocking(False)
        except (OSError, ValueError, UnicodeError):
            return
        except ValidationError:
            return


class DomainProxy:
    def __init__(self, hosts):
        self.server = ProxyServer(hosts)

    def __enter__(self):
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
