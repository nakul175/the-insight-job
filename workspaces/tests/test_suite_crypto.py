import base64
import hashlib
import json
import time
from unittest.mock import patch

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from django.test import SimpleTestCase, override_settings

from workspaces.suite_oidc import OIDCError, exchange_code, validate_id_token


@override_settings(
    APLYD_SOURCE_OIDC_ENABLED=True,
    APLYD_SOURCE_ORIGIN="https://source.invalid",
    APLYD_ACCOUNTS_ISSUER="https://accounts.aplyd.org",
    APLYD_SOURCE_CLIENT_ID="sourcealmighty",
    APLYD_SOURCE_CLIENT_SECRET="synthetic-only",
)
class IDTokenTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.jwk = dict(
            json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(cls.key.public_key())),
            kid="synthetic",
            use="sig",
            alg="RS256",
        )

    def claims(self, **overrides):
        now = int(time.time())
        return dict(
            {
                "iss": "https://accounts.aplyd.org",
                "sub": "synthetic-sub",
                "aud": "sourcealmighty",
                "iat": now,
                "exp": now + 120,
                "nonce": "synthetic-nonce",
                "aplyd_session_uid": "synthetic-session",
            },
            **overrides,
        )

    def sign(self, claims=None, key=None, kid="synthetic"):
        return jwt.encode(
            claims or self.claims(),
            key or self.key,
            algorithm="RS256",
            headers={"kid": kid},
        )

    @patch("workspaces.suite_oidc._request_json")
    def test_signed_token_accepted_and_fixed_key_endpoint(self, request):
        request.return_value = {"keys": [self.jwk]}
        self.assertEqual(
            validate_id_token(self.sign(), "synthetic-nonce", "access")["sub"],
            "synthetic-sub",
        )
        self.assertEqual(
            request.call_args.args[0].full_url, "https://accounts.aplyd.org/jwks"
        )

    @patch("workspaces.suite_oidc._request_json")
    def test_invalid_claims_signatures_keys_and_algorithms_denied(self, request):
        request.return_value = {"keys": [self.jwk]}
        cases = [
            self.sign(self.claims(iss="https://wrong.invalid")),
            self.sign(self.claims(aud="other")),
            self.sign(self.claims(exp=int(time.time()) - 1)),
            self.sign(self.claims(iat=int(time.time()) + 300)),
            self.sign(self.claims(nonce="wrong")),
            self.sign(self.claims(aud=["sourcealmighty", "other"])),
            self.sign(self.claims(azp="other")),
            self.sign(key=self.other),
            self.sign(kid="unknown"),
            jwt.encode(
                self.claims(),
                "synthetic-hmac-secret",
                algorithm="HS256",
                headers={"kid": "synthetic"},
            ),
            jwt.encode(self.claims(), "", algorithm="none"),
            "not-a-jwt",
        ]
        missing = self.claims()
        del missing["exp"]
        cases.append(self.sign(missing))
        for token in cases:
            with self.subTest(case=cases.index(token)), self.assertRaises(OIDCError):
                validate_id_token(token, "synthetic-nonce", "access")

    @patch("workspaces.suite_oidc._request_json")
    def test_duplicate_key_and_malformed_jwks_denied(self, request):
        for document in (
            {"keys": [self.jwk, self.jwk]},
            {"keys": []},
            [],
            {"keys": "invalid"},
        ):
            request.return_value = document
            with self.assertRaises(OIDCError):
                validate_id_token(self.sign(), "synthetic-nonce", "access")

    @patch("workspaces.suite_oidc._request_json")
    def test_access_token_hash_binding(self, request):
        request.return_value = {"keys": [self.jwk]}
        digest = (
            base64.urlsafe_b64encode(hashlib.sha256(b"access").digest()[:16])
            .rstrip(b"=")
            .decode()
        )
        token = self.sign(self.claims(at_hash=digest))
        validate_id_token(token, "synthetic-nonce", "access")
        with self.assertRaises(OIDCError):
            validate_id_token(token, "synthetic-nonce", "different")

    @patch("workspaces.suite_oidc._request_json")
    def test_userinfo_subject_and_session_must_match_signed_token(self, request):
        profile = {
            "sub": "synthetic-sub",
            "email": "synthetic@example.invalid",
            "email_verified": True,
            "aplyd_session_uid": "synthetic-session",
        }
        pending = {"state": "state", "nonce": "synthetic-nonce", "verifier": "verifier"}
        for mismatch in ({"sub": "different"}, {"aplyd_session_uid": "different"}):
            request.side_effect = [
                {"access_token": "access", "id_token": self.sign()},
                {"keys": [self.jwk]},
                dict(profile, **mismatch),
            ]
            with self.assertRaises(OIDCError):
                exchange_code(code="code", state="state", pending=pending)
        request.side_effect = [
            {"access_token": "access", "id_token": self.sign()},
            {"keys": [self.jwk]},
            profile,
        ]
        self.assertEqual(
            exchange_code(code="code", state="state", pending=pending)["uid"],
            "synthetic-session",
        )
