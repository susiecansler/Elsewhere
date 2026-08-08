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


def state(client, **params):
    return client.get("/api/state", params=params).json()


def review(client, reviewer, name, answer, city="chicago"):
    return client.post(
        "/api/review",
        json={
            "reviewer": reviewer,
            "source_name": name,
            "source_city": "austin",
            "target_city": city,
            "answer": answer,
        },
    )


def row(s, name):
    return next(m for m in s["matches"] if m["name"] == name)


def first_name(client):
    return state(client)["matches"][0]["name"]


# ─── Serving ──────────────────────────────────────────────────────────────


def test_page_loads(client):
    r = client.get("/")
    assert r.status_code == 200 and "Elsewhere" in r.text


def test_healthcheck(client):
    r = client.get("/healthz")
    assert r.status_code == 200 and r.json()["matches"] >= 100


# ─── Any-to-any shape ─────────────────────────────────────────────────────


def test_state_exposes_places_with_per_city_answers(client):
    s = state(client)
    assert len(s["matches"]) >= 100
    m = s["matches"][0]
    assert {"name", "roles", "cities"} <= set(m)
    assert s["targets"], "must report which cities it can answer in"
    for city in s["targets"]:
        assert m["cities"][city]["candidates"]


def test_state_lists_every_source_city(client):
    s = state(client)
    assert "austin" in s["sources"]
    # Each source maps to the cities it can answer in.
    assert all(isinstance(v, list) and v for v in s["sources"].values())


def test_austin_answers_in_both_cities_when_both_are_generated(client):
    pairs = web.available_pairs()
    targets = sorted(t for src, t in pairs if src == "austin")
    if len(targets) < 2:
        pytest.skip("only one target generated for austin")

    s = state(client, source="austin")
    assert s["targets"] == targets
    m = row(s, "H-E-B")
    # A different city means a genuinely different answer.
    answers = {t: m["cities"][t]["candidates"][0]["name"] for t in targets}
    assert len(set(answers.values())) > 1, answers


def test_unknown_source_falls_back_rather_than_erroring(client):
    s = state(client, source="atlantis")
    assert s["source"] in s["sources"]


# ─── Reviewing ────────────────────────────────────────────────────────────


def test_review_round_trips(client):
    name = first_name(client)
    assert review(client, "Sam", name, "Somewhere").json()["ok"]
    assert row(state(client, reviewer="Sam"), name)["cities"]["chicago"]["mine"] == "Somewhere"


def test_blank_reviewer_is_rejected(client):
    assert review(client, "   ", first_name(client), "Somewhere").status_code == 400


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

    c = row(state(client, reviewer="Sam"), name)["cities"]["chicago"]
    assert c["mine"] == "Sam's pick"
    assert c["others"] == ["Alex's pick"]


def test_undo_removes_only_your_own(client):
    name = first_name(client)
    review(client, "Sam", name, "Sam's pick")
    review(client, "Alex", name, "Alex's pick")

    client.delete(
        "/api/review",
        params={"reviewer": "Sam", "source_name": name, "target_city": "chicago"},
    )

    assert row(state(client, reviewer="Sam"), name)["cities"]["chicago"]["mine"] is None
    assert row(state(client, reviewer="Alex"), name)["cities"]["chicago"]["mine"] == "Alex's pick"


def test_judged_counts_places_not_judgments(client):
    """Three people judging one place is one data point, not three."""
    name = first_name(client)
    review(client, "Sam", name, "A")
    review(client, "Alex", name, "A")
    assert review(client, "Jo", name, "B").json()["judged"] == 1


# ─── Per-city isolation ───────────────────────────────────────────────────


def test_judging_one_city_leaves_the_other_untouched(client):
    """ "Chicago's answer is right, Portland's is nonsense" must be sayable."""
    targets = state(client)["targets"]
    if len(targets) < 2:
        pytest.skip("only one target generated")

    a, b = targets[0], targets[1]
    name = first_name(client)
    review(client, "Sam", name, "Answer for " + a, city=a)

    cities = row(state(client, reviewer="Sam"), name)["cities"]
    assert cities[a]["mine"] == "Answer for " + a
    assert cities[b]["mine"] is None, "a judgment leaked between cities"


def test_delete_requires_a_city(client):
    # Defaulting would delete someone's answer for the wrong city.
    r = client.delete("/api/review", params={"reviewer": "Sam", "source_name": first_name(client)})
    assert r.status_code == 422


def test_delete_targets_only_the_named_city(client):
    targets = state(client)["targets"]
    if len(targets) < 2:
        pytest.skip("only one target generated")

    a, b = targets[0], targets[1]
    name = first_name(client)
    review(client, "Sam", name, "keep me", city=a)

    client.delete("/api/review", params={"reviewer": "Sam", "source_name": name, "target_city": b})
    assert row(state(client, reviewer="Sam"), name)["cities"][a]["mine"] == "keep me"


def test_progress_reports_the_threshold(client):
    s = state(client)
    assert s["threshold"] == evaluate.MIN_INDEPENDENT
    assert s["judged"] == 0


# ─── Discovery ────────────────────────────────────────────────────────────


def test_pairs_are_discovered_from_disk():
    """Adding a direction should just mean generating it.

    Discovery from the filesystem avoids a second place to remember to
    update, which is where a new direction would silently not appear.
    """
    pairs = web.available_pairs()
    assert ("austin", "chicago") in pairs
    assert all(len(p) == 2 and all(p) for p in pairs)
