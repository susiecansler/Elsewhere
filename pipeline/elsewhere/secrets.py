"""Securely capture and store the API key.

The key is read from a hidden terminal prompt and written straight to .env.
It never appears on screen, never enters shell history (unlike
`export ANTHROPIC_API_KEY=...`), and is never echoed back.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from elsewhere.taxonomy import REPO_ROOT

ENV_PATH = REPO_ROOT / ".env"
KEY_NAME = "ANTHROPIC_API_KEY"

#: Anthropic keys are `sk-ant-` followed by an opaque token. Checked only to
#: catch obvious paste errors (a URL, a truncated copy) — not as real
#: validation, which is what `check_key` is for.
KEY_PATTERN = re.compile(r"^sk-ant-[A-Za-z0-9_\-]{20,}$")


def looks_like_key(value: str) -> tuple[bool, str]:
    """Cheap local sanity check. Returns (ok, reason)."""
    value = value.strip()
    if not value:
        return False, "empty"
    if value.startswith(("http://", "https://")):
        return False, "that's a URL — you want the key itself, which starts with 'sk-ant-'"
    if not value.startswith("sk-ant-"):
        return False, "Anthropic keys start with 'sk-ant-'"
    if len(value) < 30:
        return False, "looks truncated — copy the whole key"
    if not KEY_PATTERN.match(value):
        return False, "contains unexpected characters — check for stray spaces or line breaks"
    return True, ""


def redact(value: str) -> str:
    """Safe-to-display form. Enough to confirm which key, not enough to use."""
    value = value.strip()
    if len(value) < 16:
        return "sk-ant-…"
    return f"{value[:11]}…{value[-4:]}"


def existing_key(path: Path | None = None) -> str | None:
    """Current key in .env, if any."""
    path = path or ENV_PATH
    if not path.exists():
        return None
    for line in path.read_text().splitlines():
        if line.strip().startswith(f"{KEY_NAME}="):
            value = line.split("=", 1)[1].strip().strip("\"'")
            return value or None
    return None


def write_key(value: str, path: Path | None = None) -> Path:
    """Write the key to .env, preserving any other variables already there.

    Sets 0600 so it isn't world-readable — the default umask would otherwise
    leave a live credential readable by every account on the machine.
    """
    path = path or ENV_PATH
    value = value.strip()

    lines = path.read_text().splitlines() if path.exists() else []
    replaced = False
    out = []
    for line in lines:
        if line.strip().startswith(f"{KEY_NAME}="):
            out.append(f"{KEY_NAME}={value}")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.append(f"{KEY_NAME}={value}")

    path.write_text("\n".join(out).rstrip("\n") + "\n")
    os.chmod(path, 0o600)
    return path


@dataclass
class KeyCheck:
    ok: bool
    detail: str
    #: True when the key authenticates but the account can't pay. Separated
    #: because it's by far the most common failure and has a different fix.
    needs_credits: bool = False


def check_key(value: str) -> KeyCheck:
    """Verify the key against the real API.

    Sends a 1-token request rather than only counting tokens: token counting
    validates authentication but not billing, and "valid key, no credits" is
    the failure people actually hit. This costs a fraction of a cent and
    answers both questions at once.
    """
    try:
        from anthropic import (
            Anthropic,
            APIConnectionError,
            APIStatusError,
            AuthenticationError,
            PermissionDeniedError,
        )
    except ImportError:  # pragma: no cover
        return KeyCheck(False, "anthropic SDK not installed — run `make setup`")

    client = Anthropic(api_key=value.strip())
    try:
        client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=1,
            messages=[{"role": "user", "content": "hi"}],
        )
    except AuthenticationError:
        return KeyCheck(False, "the API rejected this key (401) — check you copied all of it")
    except PermissionDeniedError as exc:
        return KeyCheck(False, f"key lacks permission (403): {exc}", needs_credits=True)
    except APIConnectionError:
        return KeyCheck(False, "couldn't reach the API — check your network")
    except APIStatusError as exc:
        text = str(exc).lower()
        if "credit" in text or "billing" in text or "quota" in text:
            return KeyCheck(False, "key is valid but the account has no credits", True)
        return KeyCheck(False, f"API error {exc.status_code}: {exc}")
    except Exception as exc:  # pragma: no cover
        return KeyCheck(False, f"unexpected: {type(exc).__name__}: {exc}")

    return KeyCheck(True, "authenticated, and the account can pay for requests")
