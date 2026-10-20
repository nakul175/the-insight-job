import base64
import json
import os
import subprocess
import sys
from pathlib import Path


def parse_document(data, media_type):
    if len(data) > 2_000_000:
        return {
            "status": "unsupported",
            "segments": [],
            "reason": "Document byte limit exceeded; original retained",
        }
    try:
        result = subprocess.run(
            [sys.executable, "-m", "intelligence.parse_worker"],
            input=json.dumps(
                {"data": base64.b64encode(data).decode(), "media_type": media_type}
            ).encode(),
            capture_output=True,
            timeout=7,
            check=True,
            cwd=Path(__file__).resolve().parents[1],
            env={"PATH": os.defpath, "LANG": "C.UTF-8"},
        )
        if len(result.stdout) > 2_000_000:
            raise ValueError()
        return json.loads(result.stdout)
    except (subprocess.SubprocessError, OSError, ValueError):
        return {
            "status": "failed",
            "segments": [],
            "reason": "Parser exhausted its resource budget; original retained",
        }
