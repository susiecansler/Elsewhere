from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from elsewhere import evaluate, generate, store, verify, web

pytestmark = pytest.mark.skipif(
    not (
        verify.verified_path("austin", "chicago").exists()
        or generate.raw_path("austin", "chicago").exists()
    ),
    reason="requires a generated corpus",
)


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Isolate the judgment store so tests never touch real reviews."""
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "judgments.db")
    return TestClient(web.create_app("austin", "chicago"))


def review(client, reviewer, name, answer):
    return client.post(
        "/api/review",
        json={
            "reviewer": reviewer,
            "source_name": name,
            "source_city": "austin",
            "target_city": "chicago",
            "answer": answer,
        },
    )


def first_name(client):
    return client.get("/api/state").json()["matches"][0]["name"]


# ─── Serving ──────────────────────────────────────────────────────────────


def test_page_loads(client):
    r = client.get("/")
    assert r.status_code == 200 and "Elsewhere" in r.text


def test_healthcheck(client):
    r = client.get("/healthz")
    assert r.status_code == 200 and r.json()["matches"] >= 100


def test_state_exposes_the_corpus(client):
    s = client.get("/api/state").json()
    assert len(s["matches"]) >= 100
    assert {"name", "roles", "candidates", "mine", "others"} <= set(s["matches"][0])


# ─── Reviewing ────────────────────────────────────────────────────────────


def test_review_round_trips(client):
    name = first_name(client)
    assert review(client, "Sam", name, "Somewhere").json()["ok"]

    s = client.get("/api/state", params={"reviewer": "Sam"}).json()
    assert next(m for m in s["matches"] if m["name"] == name)["mine"] == "Somewhere"


def test_blank_reviewer_is_rejected(client):
    r = review(client, "   ", first_name(client), "Somewhere")
    assert r.status_code == 400


def test_missing_reviewer_field_is_rejected(client):
    r = client.post(
        "/api/review",
        json={
            "source_name": first_name(client),
            "source_city": "austin",
            "target_city": "chicago",
            "answer": "Somewhere",
        },
    )
    assert r.status_code == 422


def test_reviewers_see_only_their_own_picks(client):
    """Colleagues must not appear to have already answered for you."""
    name = first_name(client)
    review(client, "Sam", name, "Sam's pick")
    review(client, "Alex", name, "Alex's pick")

    sam = client.get("/api/state", params={"reviewer": "Sam"}).json()
    row = next(m for m in sam["matches"] if m["name"] == name)
    assert row["mine"] == "Sam's pick"
    assert row["others"] == ["Alex's pick"]


def test_undo_removes_only_your_own(client):
    name = first_name(client)
    review(client, "Sam", name, "Sam's pick")
    review(client, "Alex", name, "Alex's pick")

    client.delete("/api/review", params={"reviewer": "Sam", "source_name": name})

    sam = client.get("/api/state", params={"reviewer": "Sam"}).json()
    alex = client.get("/api/state", params={"reviewer": "Alex"}).json()
    assert next(m for m in sam["matches"] if m["name"] == name)["mine"] is None
    assert next(m for m in alex["matches"] if m["name"] == name)["mine"] == "Alex's pick"


def test_judged_counts_places_not_judgments(client):
    """Three people judging one place is one data point, not three."""
    name = first_name(client)
    review(client, "Sam", name, "A")
    review(client, "Alex", name, "A")
    r = review(client, "Jo", name, "B")
    assert r.json()["judged"] == 1


# ─── Multiple city pairs ──────────────────────────────────────────────────


def test_pairs_are_discovered_from_disk():
    """Adding a city pair should just mean generating it.

    Discovery from the filesystem avoids a second place to remember to
    update, which is where a new pair would otherwise silently not appear.
    """
    pairs = web.available_pairs()
    assert ("austin", "chicago") in pairs
    assert all(len(p) == 2 and all(p) for p in pairs)


def test_state_lists_every_pair(client):
    s = client.get("/api/state").json()
    assert s["pairs"]
    assert {"source", "target"} <= set(s["pairs"][0])


def test_switching_pair_changes_the_corpus(client):
    pairs = web.available_pairs()
    if len(pairs) < 2:
        pytest.skip("only one corpus generated")

    a, b = pairs[0], pairs[1]
    first = client.get("/api/state", params={"source": a[0], "target": a[1]}).json()
    second = client.get("/api/state", params={"source": b[0], "target": b[1]}).json()

    assert (first["source"], first["target"]) == a
    assert (second["source"], second["target"]) == b
    # Different target city means different answers.
    assert (
        first["matches"][0]["candidates"][0]["name"]
        != second["matches"][0]["candidates"][0]["name"]
    )


def test_unknown_pair_falls_back_rather_than_erroring(client):
    s = client.get("/api/state", params={"source": "atlantis", "target": "narnia"}).json()
    assert (s["source"], s["target"]) in web.available_pairs()


def test_judgments_are_scoped_per_target_city(client):
    """A pick for Chicago must not appear as a pick for Portland."""
    pairs = web.available_pairs()
    if len(pairs) < 2:
        pytest.skip("only one corpus generated")

    (s1, t1), (s2, t2) = pairs[0], pairs[1]
    name = client.get("/api/state", params={"source": s1, "target": t1}).json()["matches"][0][
        "name"
    ]

    client.post(
        "/api/review",
        json={
            "reviewer": "Sam",
            "source_name": name,
            "source_city": s1,
            "target_city": t1,
            "answer": "Answer for " + t1,
        },
    )

    other = client.get("/api/state", params={"reviewer": "Sam", "source": s2, "target": t2}).json()
    row = next((m for m in other["matches"] if m["name"] == name), None)
    if row is not None:
        assert row["mine"] is None, "a judgment leaked across city pairs"


def test_delete_targets_the_right_city(client):
    pairs = web.available_pairs()
    if len(pairs) < 2:
        pytest.skip("only one corpus generated")

    (s1, t1), (_, t2) = pairs[0], pairs[1]
    name = client.get("/api/state", params={"source": s1, "target": t1}).json()["matches"][0][
        "name"
    ]
    body = {"reviewer": "Sam", "source_name": name, "source_city": s1, "answer": "X"}
    client.post("/api/review", json={**body, "target_city": t1})

    # Deleting against the *other* city must leave this one alone.
    client.delete("/api/review", params={"reviewer": "Sam", "source_name": name, "target_city": t2})
    still = client.get("/api/state", params={"reviewer": "Sam", "source": s1, "target": t1}).json()
    assert next(m for m in still["matches"] if m["name"] == name)["mine"] == "X"


def test_progress_reports_the_threshold(client):
    s = client.get("/api/state").json()
    assert s["threshold"] == evaluate.MIN_INDEPENDENT
    assert s["judged"] == 0
