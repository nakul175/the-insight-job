import json
import os

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.core.exceptions import ImproperlyConfigured, ValidationError


def vault():
    values = [
        v.strip()
        for v in os.environ.get("TIJ_CONNECTION_KEYS", "").split(",")
        if v.strip()
    ]
    if not values:
        raise ImproperlyConfigured(
            "Configure TIJ_CONNECTION_KEYS in the secret store before connecting accounts."
        )
    try:
        return MultiFernet([Fernet(v.encode()) for v in values])
    except (ValueError, TypeError) as exc:
        raise ImproperlyConfigured(
            "Invalid connection encryption key configuration."
        ) from exc


def seal(value):
    return vault().encrypt(json.dumps(value).encode()).decode()


def unseal(value):
    try:
        return json.loads(vault().decrypt(value.encode(), ttl=None))
    except (InvalidToken, ValueError, TypeError) as exc:
        raise ValidationError(
            "Connection material is unavailable; reconnect the account."
        ) from exc
