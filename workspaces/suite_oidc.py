"""Small fail-closed OIDC authorization-code client for Aplyd Accounts."""

import base64
import hashlib
import http.client
import json
import math
import secrets
import urllib.error
import urllib.parse
import urllib.request

import jwt
from django.conf import settings


class OIDCError(Exception):
    pass


def _endpoint(path):
    validate_config()
    return settings.APLYD_ACCOUNTS_ISSUER.rstrip("/") + path


def _callback():
    return settings.APLYD_SOURCE_ORIGIN.rstrip("/") + "/auth/callback/"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward client secrets or bearer credentials to a redirect target.
        raise OIDCError("Accounts endpoint redirected unexpectedly")


def _request_json(request):
    try:
        with urllib.request.build_opener(_NoRedirect()).open(
            request, timeout=5
        ) as response:
            if response.status != 200:
                raise OIDCError("Accounts returned an unexpected response")
            payload = response.read(65537)
            if len(payload) > 65536:
                raise OIDCError("Accounts response too large")
            return json.loads(payload)
    except (
        urllib.error.URLError,
        TimeoutError,
        http.client.HTTPException,
        ConnectionError,
        ValueError,
        json.JSONDecodeError,
    ) as error:
        raise OIDCError("Accounts is temporarily unavailable") from error


def authorization_url():
    if not settings.APLYD_SOURCE_CLIENT_SECRET:
        raise OIDCError("Portal client is not configured")
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    params = urllib.parse.urlencode(
        {
            "client_id": settings.APLYD_SOURCE_CLIENT_ID,
            "redirect_uri": _callback(),
            "response_type": "code",
            "scope": "openid email profile aplyd",
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    return _endpoint("/auth") + "?" + params, {
        "state": state,
        "nonce": nonce,
        "verifier": verifier,
    }


def exchange_code(*, code, state, pending):
    expected = pending.get("state") if isinstance(pending, dict) else None
    if (
        not isinstance(expected, str)
        or not expected
        or not isinstance(state, str)
        or not state
        or len(state) > 256
        or not secrets.compare_digest(expected.encode(), state.encode())
    ):
        raise OIDCError("State mismatch")
    verifier, nonce = pending.get("verifier"), pending.get("nonce")
    if not isinstance(verifier, str) or not verifier:
        raise OIDCError("PKCE verifier missing or invalid")
    if not isinstance(nonce, str) or not nonce:
        raise OIDCError("Nonce missing or invalid")
    secret = settings.APLYD_SOURCE_CLIENT_SECRET
    if not secret:
        raise OIDCError("Portal client is not configured")
    body = urllib.parse.urlencode(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": _callback(),
            "code_verifier": verifier,
        }
    ).encode()
    auth = base64.b64encode(
        f"{settings.APLYD_SOURCE_CLIENT_ID}:{secret}".encode()
    ).decode()
    tokens = _request_json(
        urllib.request.Request(
            _endpoint("/token"),
            data=body,
            method="POST",
            headers={
                "Authorization": "Basic " + auth,
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
    )
    if not isinstance(tokens, dict):
        raise OIDCError("Invalid token response")
    access_token = tokens.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise OIDCError("Access token missing")
    claims = validate_id_token(tokens.get("id_token"), nonce, access_token)
    profile = _request_json(
        urllib.request.Request(
            _endpoint("/me"),
            headers={"Authorization": "Bearer " + access_token},
        )
    )
    if not isinstance(profile, dict):
        raise OIDCError("Invalid userinfo response")
    if claims is not None and (
        profile.get("sub") != claims["sub"]
        or profile.get("aplyd_session_uid") != claims.get("aplyd_session_uid")
    ):
        raise OIDCError("Userinfo identity mismatch")
    if (
        not isinstance(profile.get("sub"), str)
        or profile.get("email_verified") is not True
    ):
        raise OIDCError("Verified identity required")
    if not isinstance(profile.get("email"), str) or not profile["email"]:
        raise OIDCError("Email missing")
    return {
        "issuer": settings.APLYD_ACCOUNTS_ISSUER.rstrip("/"),
        "sub": profile["sub"],
        "email": profile["email"],
        "name": profile.get("name", ""),
        "id_token": tokens.get("id_token", ""),
        "uid": profile.get("aplyd_session_uid", ""),
    }


def logout_url(*, id_token=""):
    params = {
        "client_id": settings.APLYD_SOURCE_CLIENT_ID,
        "post_logout_redirect_uri": settings.APLYD_SOURCE_ORIGIN.rstrip("/") + "/",
    }
    if id_token:
        params["id_token_hint"] = id_token
    return _endpoint("/session/end") + "?" + urllib.parse.urlencode(params)


def current_session(*, sub, uid, local_id):
    if not all(
        isinstance(value, str) and 0 < len(value) <= 200 for value in (sub, uid)
    ):
        return False
    if not settings.APLYD_SOURCE_CLIENT_SECRET:
        return False
    body = json.dumps(
        {
            "client_id": settings.APLYD_SOURCE_CLIENT_ID,
            "client_secret": settings.APLYD_SOURCE_CLIENT_SECRET,
            "sub": sub,
            "uid": uid,
        }
    ).encode()
    status = _request_json(
        urllib.request.Request(
            _endpoint("/internal/session/status"),
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
    )
    return (
        isinstance(status, dict)
        and status.get("active") is True
        and status.get("sub") == sub
        and status.get("local_id") == local_id
    )


def _allows_jwk_verification(key):
    if "key_ops" not in key:
        return True
    operations = key["key_ops"]
    # RFC 7517 section 4.3: optional array of unique, case-sensitive strings.
    return (
        isinstance(operations, list)
        and all(isinstance(operation, str) for operation in operations)
        and len(operations) == len(set(operations))
        and "verify" in operations
    )


def validate_id_token(token, nonce, access_token):
    """Validate only the configured Accounts issuer and its fixed JWKS endpoint.

    Header-provided URLs/algorithms never select trust. No decode-without-verify path.
    """
    if not isinstance(token, str) or not token or len(token) > 32768:
        raise OIDCError("ID token missing or invalid")
    if not isinstance(nonce, str) or not nonce:
        raise OIDCError("Nonce missing")
    try:
        header = jwt.get_unverified_header(token)
        if (
            header.get("alg") != "RS256"
            or not isinstance(header.get("kid"), str)
            or header.get("crit")
        ):
            raise OIDCError("Unsupported ID token header")
        document = _request_json(urllib.request.Request(_endpoint("/jwks")))
        keys = document.get("keys") if isinstance(document, dict) else None
        if not isinstance(keys, list):
            raise OIDCError("Invalid signing keys")
        matching = [
            key
            for key in keys
            if isinstance(key, dict)
            and key.get("kid") == header["kid"]
            and key.get("kty") == "RSA"
            and key.get("use", "sig") == "sig"
            and key.get("alg", "RS256") == "RS256"
            and _allows_jwk_verification(key)
        ]
        if len(matching) != 1:
            raise OIDCError("Signing key unavailable")
        key = jwt.PyJWK.from_dict(matching[0], algorithm="RS256").key
        claims = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            issuer=settings.APLYD_ACCOUNTS_ISSUER.rstrip("/"),
            audience=settings.APLYD_SOURCE_CLIENT_ID,
            options={"require": ["iss", "sub", "aud", "exp", "iat", "nonce"]},
        )
        if any(
            type(claims[k]) not in (int, float) or not math.isfinite(claims[k])
            for k in ("exp", "iat")
        ):
            raise OIDCError("Invalid timestamp")
        if (
            not isinstance(claims["sub"], str)
            or not 1 <= len(claims["sub"]) <= 200
            or not isinstance(claims.get("nonce"), str)
            or not secrets.compare_digest(claims["nonce"].encode(), nonce.encode())
        ):
            raise OIDCError("ID token nonce mismatch")
        audiences = (
            claims["aud"] if isinstance(claims["aud"], list) else [claims["aud"]]
        )
        if (len(audiences) > 1 or "azp" in claims) and claims.get(
            "azp"
        ) != settings.APLYD_SOURCE_CLIENT_ID:
            raise OIDCError("ID token authorized party mismatch")
        if "at_hash" in claims:
            expected = (
                base64.urlsafe_b64encode(
                    hashlib.sha256(access_token.encode()).digest()[:16]
                )
                .rstrip(b"=")
                .decode()
            )
            if not isinstance(claims["at_hash"], str) or not secrets.compare_digest(
                claims["at_hash"].encode(), expected.encode()
            ):
                raise OIDCError("Access token hash mismatch")
        return claims
    except (jwt.PyJWTError, ValueError, TypeError, KeyError) as error:
        raise OIDCError("ID token validation failed") from error


def validate_config():
    if not settings.APLYD_SOURCE_OIDC_ENABLED:
        raise OIDCError("Central login disabled")
    for value in (settings.APLYD_ACCOUNTS_ISSUER, settings.APLYD_SOURCE_ORIGIN):
        parsed = urllib.parse.urlsplit(value)
        if (
            parsed.username
            or parsed.password
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
            or not parsed.hostname
            or parsed.scheme not in ("http", "https")
            or (
                parsed.scheme == "http"
                and (
                    not settings.DEBUG
                    or parsed.hostname not in ("localhost", "127.0.0.1", "::1")
                )
            )
        ):
            raise OIDCError("Invalid trusted origin")
    if (
        not settings.APLYD_SOURCE_CLIENT_SECRET
        or settings.APLYD_SOURCE_CLIENT_ID
        not in ("sourcealmighty", "sourcealmighty-staging")
    ):
        raise OIDCError("Central client unavailable")
