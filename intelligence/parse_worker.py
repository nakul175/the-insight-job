"""Bounded parser subprocess: no Django, credentials, network or tool execution."""

import base64
import io
import json
import resource
import sys

from bs4 import BeautifulSoup
from pypdf import PdfReader
from pypdf.errors import PdfReadError


def parse(data, media_type):
    if len(data) > 2_000_000:
        return {
            "status": "unsupported",
            "segments": [],
            "reason": "Document byte limit exceeded",
        }
    segments = []
    if data.startswith(b"%PDF-"):
        reader = PdfReader(io.BytesIO(data), strict=True)
        if reader.is_encrypted:
            return {
                "status": "encrypted",
                "segments": [],
                "reason": "Encrypted PDF; original retained",
            }
        if len(reader.pages) > 100:
            return {
                "status": "unsupported",
                "segments": [],
                "reason": "PDF page limit exceeded",
            }
        for i, page in enumerate(reader.pages):
            text = page.extract_text() or ""
            if text.strip():
                segments.append({"kind": "page", "page": i + 1, "text": text[:200000]})
    elif media_type in {"text/html", "application/xhtml+xml"}:
        soup = BeautifulSoup(data, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        for i, tag in enumerate(
            soup.find_all(["h1", "h2", "h3", "h4", "p", "li", "table"])
        ):
            text = tag.get_text(" ", strip=True)
            if text:
                segments.append(
                    {
                        "kind": "table"
                        if tag.name == "table"
                        else ("heading" if tag.name.startswith("h") else "section"),
                        "section": i + 1,
                        "text": text[:20000],
                    }
                )
        if not segments:
            text = soup.get_text(" ", strip=True)
            if text:
                segments = [{"kind": "section", "section": 1, "text": text[:200000]}]
    elif media_type.startswith("text/"):
        text = data.decode("utf-8", errors="replace").strip()
        if text:
            segments = [{"kind": "text", "section": 1, "text": text[:200000]}]
    else:
        return {
            "status": "unsupported",
            "segments": [],
            "reason": "Unsupported document format; original retained",
        }
    total = sum(len(s["text"]) for s in segments)
    if total > 200000:
        clipped = []
        remaining = 200000
        for segment in segments:
            if remaining <= 0:
                break
            value = {**segment, "text": segment["text"][:remaining]}
            clipped.append(value)
            remaining -= len(value["text"])
        return {
            "status": "partial",
            "segments": clipped,
            "reason": "Parsed text limit reached",
        }
    return {
        "status": "parsed" if segments else "empty",
        "segments": segments,
        "reason": "" if segments else "No extractable text; original retained",
    }


if __name__ == "__main__":
    resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024, 256 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_CPU, (5, 5))
    try:
        payload = json.loads(sys.stdin.buffer.read(3_000_000))
        result = parse(
            base64.b64decode(payload["data"], validate=True), payload["media_type"]
        )
    except (
        PdfReadError,
        ValueError,
        TypeError,
        KeyError,
        MemoryError,
        RecursionError,
        OverflowError,
        UnicodeError,
    ):
        result = {
            "status": "corrupt",
            "segments": [],
            "reason": "Document could not be parsed safely; original retained",
        }
    print(json.dumps(result))
