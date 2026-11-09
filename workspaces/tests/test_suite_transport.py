import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch
from urllib.request import Request

from django.test import SimpleTestCase, override_settings

from workspaces.suite_oidc import (
    OIDCError,
    _request_json,
    exchange_code,
    validate_config,
)


@override_settings(
    APLYD_SOURCE_OIDC_ENABLED=True,
    APLYD_SOURCE_ORIGIN="https://source.invalid",
    APLYD_ACCOUNTS_ISSUER="https://accounts.invalid",
    APLYD_SOURCE_CLIENT_ID="sourcealmighty",
    APLYD_SOURCE_CLIENT_SECRET="synthetic-only",
)
class TransportTests(SimpleTestCase):
    def test_pending_proof_rejected_before_network(self):
        with patch("workspaces.suite_oidc._request_json") as network:
            for pending, state in [
                (None, "x"),
                ({}, "x"),
                ({"state": "x"}, "x"),
                ({"state": "x", "verifier": "v", "nonce": None}, "x"),
                ({"state": "x", "verifier": "v", "nonce": "n"}, "wrong"),
            ]:
                with self.assertRaises(OIDCError):
                    exchange_code(code="code", state=state, pending=pending)
            network.assert_not_called()

    def test_trust_configuration_rejects_credentials_path_fragment_and_remote_http(
        self,
    ):
        for origin in (
            "http://outside.invalid",
            "https://user:pass@source.invalid",
            "https://source.invalid/path",
            "https://source.invalid#fragment",
            "file:///tmp/source",
        ):
            with (
                override_settings(APLYD_SOURCE_ORIGIN=origin),
                self.assertRaises(OIDCError),
            ):
                validate_config()
        with (
            override_settings(
                DEBUG=False, APLYD_SOURCE_ORIGIN="http://localhost:49991"
            ),
            self.assertRaises(OIDCError),
        ):
            validate_config()

    def test_redirect_never_forwards_credentials_and_response_limits_fail_closed(self):
        seen = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                seen.append(self.path)
                if self.path == "/redirect":
                    self.send_response(302)
                    self.send_header("Location", "/secret-target")
                    self.end_headers()
                    return
                self.send_response(200)
                self.end_headers()
                if self.path == "/large":
                    self.wfile.write(b" " * 65537)
                elif self.path == "/invalid":
                    self.wfile.write(b"{invalid")
                else:
                    self.wfile.write(json.dumps({"active": True}).encode())

        server = HTTPServer(("127.0.0.1", 0), Handler)
        worker = threading.Thread(
            target=server.serve_forever, kwargs={"poll_interval": 0.01}
        )
        worker.start()
        try:
            for path in ("/redirect", "/large", "/invalid"):
                with self.assertRaises(OIDCError):
                    _request_json(
                        Request(
                            f"http://127.0.0.1:{server.server_port}" + path,
                            headers={"Authorization": "Bearer synthetic-only"},
                        )
                    )
            self.assertNotIn("/secret-target", seen)
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=1)
            self.assertFalse(worker.is_alive())
