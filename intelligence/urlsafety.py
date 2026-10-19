import ipaddress
import socket
from urllib.parse import unquote, urlsplit, urlunsplit

from django.core.exceptions import ValidationError


def canonical_url(value, resolve=True):
    try:
        p = urlsplit(value.strip())
        if (
            p.scheme not in {"http", "https"}
            or not p.hostname
            or p.username
            or p.password
            or p.port not in {None, 80, 443}
        ):
            raise ValueError()
        host = p.hostname.encode("idna").decode("ascii").lower().rstrip(".")
        if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
            raise ValueError()
        try:
            addresses = [ipaddress.ip_address(host)]
        except ValueError:
            if not resolve:
                addresses = []
            else:
                addresses = [
                    ipaddress.ip_address(x[4][0])
                    for x in socket.getaddrinfo(
                        host,
                        p.port or (443 if p.scheme == "https" else 80),
                        type=socket.SOCK_STREAM,
                    )
                ]
        if any(not ip.is_global for ip in addresses):
            raise ValueError()
        if resolve and not addresses:
            raise ValueError()
        netloc = f"[{host}]" if ":" in host else host
        if p.port and p.port != (443 if p.scheme == "https" else 80):
            netloc += f":{p.port}"
        return urlunsplit((p.scheme.lower(), netloc, p.path or "/", p.query, ""))
    except (ValueError, UnicodeError, OSError) as exc:
        raise ValidationError(
            "Use a valid public HTTP(S) URL on port 80 or 443; private networks, credentials and unsupported URLs are blocked."
        ) from exc


def scope_decision(base, url, config):
    canonical = canonical_url(url)
    origin = urlsplit(base)
    target = urlsplit(canonical)
    if (origin.scheme, origin.netloc) != (target.scheme, target.netloc):
        return False, "Outside the approved origin"
    path = unquote(target.path)
    if ".." in path.split("/") or "\\" in path:
        return False, "Ambiguous path"
    for rule in config.get("exclude", []):
        if path.startswith(rule):
            return False, f"Excluded by {rule}"
    for rule in config.get("include", []):
        if path.startswith(rule):
            return True, f"Included by {rule}"
    return False, "No include rule matched"
