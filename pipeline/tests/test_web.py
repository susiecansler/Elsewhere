"""The public site is a read-only lookup tool.

Reviewing and evaluation live in the CLI, not here — these tests pin that
separation as much as they pin the lookup behaviour.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

import pytest
from fastapi.testclient import TestClient

from elsewhere import generate, links, verify, web

pytestmark = pytest.mark.skipif(
    not (
        verify.verified_path("austin", "chicago").exists()
        or generate.raw_path("austin", "chicago").exists()
    ),
    reason="requires a generated corpus",
)


@pytest.fixture
def client():
    return TestClient(web.create_app("austin", "chicago"))


def state(client, **params):
    return client.get("/api/state", params=params).json()


def row(s, name):
    return next(m for m in s["matches"] if m["name"] == name)


# ─── Serving ──────────────────────────────────────────────────────────────


def test_page_loads(client):
    r = client.get("/")
    assert r.status_code == 200 and "Elsewhere" in r.text


def test_healthcheck(client):
    r = client.get("/healthz")
    assert r.status_code == 200 and r.json()["matches"] >= 100


def test_cities_lists_every_starting_point(client):
    cities = client.get("/api/cities").json()
    assert "austin" in cities
    assert cities == sorted(cities)


# ─── Read-only ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("method,path", [("post", "/api/review"), ("delete", "/api/review")])
def test_no_write_endpoints(client, method, path):
    """Nothing on the public site should accept writes.

    The review flow was removed on purpose; leaving its endpoints behind
    would keep an unauthenticated write surface on an open URL.
    """
    r = getattr(client, method)(path, json={}) if method == "post" else client.delete(path)
    assert r.status_code == 404


def test_state_carries_no_reviewer_data(client):
    """No per-visitor state — the corpus is the same for everyone."""
    s = state(client)
    assert "reviewers" not in s and "judged" not in s
    city = s["matches"][0]["cities"][s["targets"][0]]
    assert set(city) == {"candidates"}


# ─── Lookup ───────────────────────────────────────────────────────────────


def test_state_groups_answers_by_place(client):
    s = state(client)
    assert len(s["matches"]) >= 100
    m = s["matches"][0]
    assert {"name", "aliases", "roles", "cities"} <= set(m)
    assert s["targets"]
    for city in s["targets"]:
        cands = m["cities"][city]["candidates"]
        assert cands and {"name", "reasoning"} <= set(cands[0])


def test_every_city_works_as_a_starting_point(client):
    for src in client.get("/api/cities").json():
        s = state(client, source=src)
        assert s["source"] == src
        assert s["targets"], f"{src} answers nowhere"
        assert src not in s["targets"], "a city should not map to itself"
        assert len(s["matches"]) >= 100


def test_the_same_place_differs_by_city(client):
    s = state(client, source="austin")
    if len(s["targets"]) < 2:
        pytest.skip("only one target generated")
    m = row(s, "H-E-B")
    answers = {t: m["cities"][t]["candidates"][0]["name"] for t in s["targets"]}
    assert len(set(answers.values())) > 1, answers


def test_reverse_direction_agrees_with_the_forward_one(client):
    """Franklin Barbecue ↔ Lou Malnati's was generated in two separate runs.

    Neither knew about the other, so agreement is a real signal that the
    role model is stable rather than an artifact of one prompt.
    """
    cities = client.get("/api/cities").json()
    if not {"austin", "chicago"} <= set(cities):
        pytest.skip("needs both directions generated")

    fwd = row(state(client, source="austin"), "Franklin Barbecue")
    rev = row(state(client, source="chicago"), "Lou Malnati's")
    assert fwd["cities"]["chicago"]["candidates"][0]["name"] == "Lou Malnati's"
    assert rev["cities"]["austin"]["candidates"][0]["name"] == "Franklin Barbecue"


def test_unknown_source_falls_back_rather_than_erroring(client):
    s = state(client, source="atlantis")
    assert s["source"] in s["sources"]


# ─── Discovery ────────────────────────────────────────────────────────────


def test_pairs_are_discovered_from_disk():
    """Adding a direction should just mean generating it."""
    pairs = web.available_pairs()
    assert ("austin", "chicago") in pairs
    assert all(len(p) == 2 and all(p) for p in pairs)


# ─── Links ────────────────────────────────────────────────────────────────


def test_every_place_gets_a_map_link(client):
    """Map links are derived from the name, so they never depend on a build."""
    s = state(client)
    m = s["matches"][0]
    assert m["links"]["map"].startswith("https://www.google.com/maps/")
    for city in s["targets"]:
        assert m["cities"][city]["candidates"][0]["links"]["map"]


def test_websites_are_http_only():
    """Upstream data becomes an href, so anything but http(s) must be dropped."""
    assert links._clean("javascript:alert(1)") is None
    assert links._clean(" https://heb.com ") == "https://heb.com"
    assert links._clean(None) is None


def test_map_link_carries_the_city():
    """'Mariano's' alone lands anywhere; the city is what makes it right."""
    url = links.map_url("Mariano's", "chicago")
    assert "Chicago" in url and "Mariano" in url


def test_page_script_parses():
    """The page is one big inline script with no build step behind it.

    A syntax error there is silent: the server still returns 200, the tests
    still pass, and the page renders blank. A duplicate `const` introduced
    exactly that. Skipped where node isn't installed rather than made a hard
    dependency of the suite.
    """
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    script = web.PAGE[web.PAGE.index("<script>") + 8 : web.PAGE.rindex("</script>")]
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(script)
        tmp = fh.name
    try:
        done = subprocess.run([node, "--check", tmp], capture_output=True, text=True)
        assert done.returncode == 0, done.stderr
    finally:
        os.unlink(tmp)
