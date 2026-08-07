from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from elsewhere import evaluate, generate, verify, web

pytestmark = pytest.mark.skipif(
    not (
        verify.verified_path("austin", "chicago").exists()
        or generate.raw_path("austin", "chicago").exists()
    ),
    reason="requires a generated corpus",
)


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Point ground truth at a temp file so tests never touch the real corpus."""
    gt = tmp_path / "ground_truth.jsonl"
    monkeypatch.setattr(evaluate, "GROUND_TRUTH_PATH", gt)
    return TestClient(web.create_app("austin", "chicago"))


def test_page_loads(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Elsewhere" in r.text


def test_state_exposes_the_corpus(client):
    s = client.get("/api/state").json()
    assert s["source"] == "austin" and s["target"] == "chicago"
    assert len(s["matches"]) >= 100
    first = s["matches"][0]
    assert {"name", "roles", "candidates", "reviewed"} <= set(first)
    assert first["candidates"]


def test_review_round_trips(client):
    name = client.get("/api/state").json()["matches"][0]["name"]
    r = client.post(
        "/api/review",
        json={
            "source_name": name,
            "source_city": "austin",
            "target_city": "chicago",
            "accepted": ["Somewhere"],
        },
    )
    assert r.status_code == 200 and r.json()["independent"] == 1

    match = next(m for m in client.get("/api/state").json()["matches"] if m["name"] == name)
    assert match["reviewed"] == ["Somewhere"]


def test_rejudging_replaces_rather_than_duplicates(client):
    """Clicking a different candidate must not leave two conflicting answers."""
    name = client.get("/api/state").json()["matches"][0]["name"]
    body = {"source_name": name, "source_city": "austin", "target_city": "chicago"}
    client.post("/api/review", json={**body, "accepted": ["First"]})
    r = client.post("/api/review", json={**body, "accepted": ["Second"]})

    assert r.json()["independent"] == 1, "should replace, not accumulate"
    match = next(m for m in client.get("/api/state").json()["matches"] if m["name"] == name)
    assert match["reviewed"] == ["Second"]


def test_undo_removes_the_judgment(client):
    name = client.get("/api/state").json()["matches"][0]["name"]
    client.post(
        "/api/review",
        json={
            "source_name": name,
            "source_city": "austin",
            "target_city": "chicago",
            "accepted": ["Somewhere"],
        },
    )
    r = client.delete(f"/api/review/{name}")
    assert r.json()["independent"] == 0

    match = next(m for m in client.get("/api/state").json()["matches"] if m["name"] == name)
    assert match["reviewed"] is None


def test_reviews_are_written_as_independent_evidence(client, tmp_path):
    name = client.get("/api/state").json()["matches"][0]["name"]
    client.post(
        "/api/review",
        json={
            "source_name": name,
            "source_city": "austin",
            "target_city": "chicago",
            "accepted": ["Somewhere"],
        },
    )
    entries = evaluate.load_ground_truth(tmp_path / "ground_truth.jsonl")
    assert entries[0].provenance == "reviewed"
    assert entries[0].provenance in evaluate.INDEPENDENT


def test_progress_reports_the_threshold(client):
    s = client.get("/api/state").json()
    assert s["threshold"] == evaluate.MIN_INDEPENDENT
    assert s["independent"] == 0
