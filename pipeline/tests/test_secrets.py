from __future__ import annotations

import stat

import pytest

from elsewhere import secrets

FAKE = "sk-ant-api03-" + "A" * 40


# ─── Format checking ──────────────────────────────────────────────────────


def test_accepts_a_well_formed_key():
    ok, _ = secrets.looks_like_key(FAKE)
    assert ok


def test_tolerates_surrounding_whitespace():
    # Pasting from a browser routinely brings a trailing newline.
    ok, _ = secrets.looks_like_key(f"  {FAKE}\n")
    assert ok


@pytest.mark.parametrize(
    "value,fragment",
    [
        ("", "empty"),
        ("https://platform.claude.com/settings/keys", "URL"),
        ("my-secret-key", "sk-ant-"),
        ("sk-ant-short", "truncated"),
        ("sk-ant-api03-" + "A" * 20 + " " + "B" * 20, "unexpected characters"),
    ],
)
def test_rejects_bad_input_with_an_actionable_reason(value, fragment):
    ok, reason = secrets.looks_like_key(value)
    assert not ok
    assert fragment in reason


# ─── Redaction ────────────────────────────────────────────────────────────


def test_redaction_hides_the_secret():
    shown = secrets.redact(FAKE)
    assert "A" * 40 not in shown
    assert shown.startswith("sk-ant-api")
    assert len(shown) < 25


def test_redaction_survives_short_input():
    assert secrets.redact("sk-ant") == "sk-ant-…"


# ─── Storage ──────────────────────────────────────────────────────────────


def test_writes_the_key(tmp_path):
    env = tmp_path / ".env"
    secrets.write_key(FAKE, env)
    assert secrets.existing_key(env) == FAKE


def test_file_is_not_world_readable(tmp_path):
    # A live credential readable by every account on the machine is a real
    # exposure; the default umask would allow it.
    env = tmp_path / ".env"
    secrets.write_key(FAKE, env)
    mode = stat.S_IMODE(env.stat().st_mode)
    assert mode == 0o600, oct(mode)


def test_preserves_other_variables(tmp_path):
    env = tmp_path / ".env"
    env.write_text("REDDIT_CLIENT_ID=abc\nOTHER=keep-me\n")
    secrets.write_key(FAKE, env)
    body = env.read_text()
    assert "REDDIT_CLIENT_ID=abc" in body
    assert "OTHER=keep-me" in body
    assert secrets.existing_key(env) == FAKE


def test_replaces_rather_than_appending_a_second_key(tmp_path):
    env = tmp_path / ".env"
    secrets.write_key(FAKE, env)
    secrets.write_key("sk-ant-api03-" + "B" * 40, env)
    assert env.read_text().count("ANTHROPIC_API_KEY=") == 1


def test_no_existing_key_reads_as_none(tmp_path):
    assert secrets.existing_key(tmp_path / "absent.env") is None


def test_placeholder_value_is_not_mistaken_for_a_key(tmp_path):
    env = tmp_path / ".env"
    env.write_text("ANTHROPIC_API_KEY=\n")
    assert secrets.existing_key(env) is None


def test_strips_quotes_some_editors_add(tmp_path):
    env = tmp_path / ".env"
    env.write_text(f'ANTHROPIC_API_KEY="{FAKE}"\n')
    assert secrets.existing_key(env) == FAKE


# ─── Live check ───────────────────────────────────────────────────────────


def test_bad_key_is_reported_as_an_auth_failure():
    """Hits the real API with a syntactically valid but fake key."""
    result = secrets.check_key("sk-ant-api03-" + "Z" * 40)
    assert not result.ok
    assert "401" in result.detail or "rejected" in result.detail
