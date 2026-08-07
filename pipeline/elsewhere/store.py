"""Durable, concurrent-safe store for reviewer judgments.

The local flow rewrites ground_truth.jsonl on every judgment. That is fine for
one person at a terminal and wrong the moment two reviewers click at the same
time: both read the file, both append, the second write erases the first.

SQLite with WAL handles that correctly, and it gives each judgment an owner.
Attribution is what makes an open link tolerable — a stranger's answers are
identifiable and removable instead of silently mixed into the corpus.

One row per (reviewer, source_name): re-judging replaces your own answer and
never touches anyone else's. Disagreement between reviewers is preserved
rather than resolved here, because a genuine split is the signal that a role
is contested.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from elsewhere.taxonomy import REPO_ROOT

#: Overridden in deployment to point at the mounted disk. A container's
#: default filesystem is ephemeral — judgments written there vanish on the
#: next deploy.
DATA_DIR = Path(os.environ.get("ELSEWHERE_DATA_DIR", REPO_ROOT / "data" / "reviews"))
DB_PATH = DATA_DIR / "judgments.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS judgments (
    reviewer     TEXT NOT NULL,
    source_name  TEXT NOT NULL,
    source_city  TEXT NOT NULL,
    target_city  TEXT NOT NULL,
    answer       TEXT NOT NULL,
    custom       INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL,
    PRIMARY KEY (reviewer, source_name, target_city)
);
CREATE INDEX IF NOT EXISTS idx_source ON judgments(source_name, target_city);
"""


@dataclass(frozen=True)
class Judgment:
    reviewer: str
    source_name: str
    source_city: str
    target_city: str
    answer: str
    custom: bool
    created_at: str


@contextmanager
def connect(path: Path | None = None):
    path = path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), timeout=10)
    try:
        # WAL lets readers proceed during a write, which is the whole point
        # of moving off the JSONL rewrite.
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA busy_timeout=5000")
        con.executescript(SCHEMA)
        yield con
        con.commit()
    finally:
        con.close()


def normalize_reviewer(name: str) -> str:
    """Fold a display name to a stable key.

    Case and spacing shouldn't fork one person into two reviewers.
    """
    return " ".join(name.strip().split())[:60]


def record(
    reviewer: str,
    source_name: str,
    source_city: str,
    target_city: str,
    answer: str,
    custom: bool = False,
    path: Path | None = None,
) -> None:
    """Save one judgment, replacing this reviewer's previous answer."""
    reviewer = normalize_reviewer(reviewer)
    if not reviewer:
        raise ValueError("reviewer name is required")
    with connect(path) as con:
        con.execute(
            """
            INSERT INTO judgments
                (reviewer, source_name, source_city, target_city, answer, custom, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(reviewer, source_name, target_city) DO UPDATE SET
                answer = excluded.answer,
                custom = excluded.custom,
                created_at = excluded.created_at
            """,
            [
                reviewer,
                source_name,
                source_city,
                target_city,
                answer,
                int(custom),
                datetime.now(UTC).isoformat(timespec="seconds"),
            ],
        )


def forget(reviewer: str, source_name: str, target_city: str, path: Path | None = None) -> None:
    """Remove this reviewer's judgment. Leaves everyone else's intact."""
    with connect(path) as con:
        con.execute(
            "DELETE FROM judgments WHERE reviewer = ? AND source_name = ? AND target_city = ?",
            [normalize_reviewer(reviewer), source_name, target_city],
        )


def for_reviewer(reviewer: str, target_city: str, path: Path | None = None) -> dict[str, str]:
    """This reviewer's answers, keyed by source place."""
    with connect(path) as con:
        rows = con.execute(
            "SELECT source_name, answer FROM judgments WHERE reviewer = ? AND target_city = ?",
            [normalize_reviewer(reviewer), target_city],
        ).fetchall()
    return dict(rows)


def all_judgments(path: Path | None = None) -> list[Judgment]:
    with connect(path) as con:
        rows = con.execute(
            """
            SELECT reviewer, source_name, source_city, target_city, answer, custom, created_at
            FROM judgments ORDER BY source_name, reviewer
            """
        ).fetchall()
    return [Judgment(r[0], r[1], r[2], r[3], r[4], bool(r[5]), r[6]) for r in rows]


def reviewers(path: Path | None = None) -> dict[str, int]:
    """Reviewer → how many places they've judged."""
    with connect(path) as con:
        rows = con.execute(
            "SELECT reviewer, count(*) FROM judgments GROUP BY reviewer ORDER BY 2 DESC"
        ).fetchall()
    return dict(rows)


def consensus(
    target_city: str, exclude: set[str] | None = None, path: Path | None = None
) -> dict[str, dict]:
    """Aggregate judgments per place across reviewers.

    Returns `{source_name: {"accepted": [...], "reviewers": [...], "split": bool}}`.

    Every distinct answer is kept, not just the majority one. Two locals
    naming different places usually means the role is genuinely contested,
    and collapsing that to a single "right" answer would score a correct
    match as wrong — the same reason GroundTruth.accepted is a list.
    """
    exclude = {normalize_reviewer(e) for e in (exclude or set())}
    out: dict[str, dict] = {}
    for j in all_judgments(path):
        if j.target_city != target_city or j.reviewer in exclude:
            continue
        entry = out.setdefault(
            j.source_name,
            {"accepted": [], "reviewers": [], "source_city": j.source_city, "split": False},
        )
        if j.answer not in entry["accepted"]:
            entry["accepted"].append(j.answer)
        entry["reviewers"].append(j.reviewer)

    for entry in out.values():
        entry["split"] = len(entry["accepted"]) > 1
    return out


def count(path: Path | None = None) -> int:
    with connect(path) as con:
        return con.execute("SELECT count(DISTINCT source_name) FROM judgments").fetchone()[0]
