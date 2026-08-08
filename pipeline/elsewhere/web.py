"""Local viewer and review UI.

Reviewing 30 matches at a terminal prompt is tedious enough that it doesn't
get done, and the eval is blocked until it does. This is the same workflow
with the friction removed: scan the corpus, judge a match in one click, and
the judgment lands in ground_truth.jsonl immediately.

Deliberately a *local dev tool*, not the product. It binds to localhost, reads
and writes files on disk, and ships as one embedded HTML string with no build
step. The eventual public app is still the TypeScript one in `web/`.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from elsewhere import evaluate, generate, store, verify


def load_corpus(source: str, target: str) -> list:
    """Prefer the verified corpus; fall back to raw."""
    path = verify.verified_path(source, target)
    if not path.exists():
        path = generate.raw_path(source, target)
    return generate.read_matches(path)


def available_pairs() -> list[tuple[str, str]]:
    """Every city pair with a generated corpus on disk.

    Discovered from the filesystem rather than configured, so adding a pair is
    just generating it — no second place to remember to update.
    """
    pairs: set[tuple[str, str]] = set()
    for path in generate.MATCHES_DIR.glob("*-*.raw.jsonl"):
        stem = path.name.removesuffix(".raw.jsonl")
        if "-" in stem:
            source, target = stem.split("-", 1)
            pairs.add((source, target))
    for path in generate.MATCHES_DIR.glob("*-*.verified.jsonl"):
        stem = path.name.removesuffix(".verified.jsonl")
        if "-" in stem:
            source, target = stem.split("-", 1)
            pairs.add((source, target))
    return sorted(pairs)


class ReviewIn(BaseModel):
    reviewer: str = Field(min_length=1, max_length=60)
    source_name: str
    source_city: str
    target_city: str
    answer: str = Field(min_length=1, max_length=200)
    custom: bool = False


def create_app(source: str = "austin", target: str = "chicago") -> FastAPI:
    app = FastAPI(title="Elsewhere", docs_url=None, redoc_url=None)

    pairs = available_pairs() or [(source, target)]
    # Load every corpus once at startup. Together they're a few MB, and
    # re-reading per request would make switching cities feel sluggish.
    corpora = {p: load_corpus(*p) for p in pairs}
    default = (source, target) if (source, target) in corpora else pairs[0]

    def state(reviewer: str | None, pair: tuple[str, str]) -> dict[str, Any]:
        src, tgt = pair
        corpus = corpora[pair]
        mine = store.for_reviewer(reviewer, tgt) if reviewer else {}
        agreed = store.consensus(tgt)

        return {
            "source": src,
            "target": tgt,
            "pairs": [{"source": a, "target": b} for a, b in pairs],
            "threshold": evaluate.MIN_INDEPENDENT,
            "judged": len(agreed),
            "reviewers": store.reviewers(),
            "matches": [
                {
                    "name": m.source.name,
                    "category": m.source.category,
                    "roles": m.role_tags,
                    "price_tier": int(m.price_tier),
                    "reach": m.reach.value,
                    "mine": mine.get(m.source.name),
                    # Everyone's answers, so a reviewer can see they're
                    # disagreeing with someone rather than judging blind.
                    "others": [
                        a
                        for a in agreed.get(m.source.name, {}).get("accepted", [])
                        if a != mine.get(m.source.name)
                    ],
                    "candidates": [
                        {
                            "name": c.name,
                            "reasoning": c.reasoning,
                            "confidence": c.confidence,
                            "verified": c.verified,
                        }
                        for c in m.candidates
                    ],
                }
                for m in corpus
            ],
        }

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return PAGE

    @app.get("/api/state")
    def api_state(reviewer: str = "", source: str = "", target: str = "") -> JSONResponse:
        pair = (source, target) if (source, target) in corpora else default
        return JSONResponse(state(reviewer or None, pair))

    @app.post("/api/review")
    def api_review(body: ReviewIn) -> JSONResponse:
        try:
            store.record(
                reviewer=body.reviewer,
                source_name=body.source_name,
                source_city=body.source_city,
                target_city=body.target_city,
                answer=body.answer,
                custom=body.custom,
            )
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        return JSONResponse({"ok": True, "judged": store.count()})

    @app.delete("/api/review")
    def api_unreview(reviewer: str, source_name: str, target_city: str = "") -> JSONResponse:
        # Must come from the request: with several pairs loaded, defaulting to
        # the app's startup target would delete from the wrong city.
        store.forget(reviewer, source_name, target_city or default[1])
        return JSONResponse({"ok": True, "judged": store.count()})

    @app.get("/healthz")
    def healthz() -> JSONResponse:
        return JSONResponse(
            {
                "ok": True,
                "pairs": len(corpora),
                "matches": sum(len(c) for c in corpora.values()),
            }
        )

    return app


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Elsewhere</title>
<style>
:root {
  --bg: #fbfaf8; --panel: #fff; --ink: #1a1a1a; --dim: #6b6b6b;
  --line: #e5e1da; --accent: #b4542a; --ok: #2f7d4f;
  --warn: #a8761b; --chip: #f0ece5;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #16151a; --panel: #1e1d23; --ink: #eceaf0; --dim: #9b98a4;
    --line: #302e38; --accent: #e08d5f; --ok: #6cc48c; --warn: #d9ab5c;
    --chip: #292731;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--ink);
  font: 15px/1.55 ui-sans-serif, -apple-system, "Segoe UI", Roboto, sans-serif;
}
header {
  position: sticky; top: 0; z-index: 10; background: var(--bg);
  border-bottom: 1px solid var(--line); padding: 14px 20px;
}
.bar { display: flex; gap: 14px; align-items: center; flex-wrap: wrap; max-width: 1100px; margin: 0 auto; }
h1 { font-size: 17px; margin: 0; font-weight: 650; letter-spacing: -0.01em; }
h1 span { color: var(--dim); font-weight: 400; }
input[type=search], input[type=text] {
  font: inherit; padding: 7px 11px; border: 1px solid var(--line);
  border-radius: 7px; background: var(--panel); color: var(--ink); min-width: 200px;
}
.grow { flex: 1; }
.progress { font-size: 13px; color: var(--dim); white-space: nowrap; }
.progress b { color: var(--ink); }
.track { width: 130px; height: 6px; background: var(--chip); border-radius: 3px; overflow: hidden; display: inline-block; vertical-align: middle; margin-left: 8px; }
.fill { height: 100%; background: var(--ok); transition: width .3s; }
.chips { display: flex; gap: 6px; }
.chip {
  font: inherit; font-size: 13px; padding: 5px 11px; border-radius: 999px;
  border: 1px solid var(--line); background: var(--panel); color: var(--dim); cursor: pointer;
}
.chip[aria-pressed=true] { background: var(--ink); color: var(--bg); border-color: var(--ink); }
main { max-width: 1100px; margin: 0 auto; padding: 20px; }
.card {
  background: var(--panel); border: 1px solid var(--line); border-radius: 11px;
  padding: 16px 18px; margin-bottom: 14px;
}
.card.done { border-color: var(--ok); }
.head { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; margin-bottom: 3px; }
.head h2 { font-size: 16px; margin: 0; font-weight: 620; }
.arrow { color: var(--dim); }
.meta { font-size: 12.5px; color: var(--dim); margin-bottom: 13px; }
.role { display: inline-block; background: var(--chip); padding: 1px 8px; border-radius: 4px; margin-right: 5px; font-size: 12px; }
.cand { display: flex; gap: 11px; padding: 9px 0; border-top: 1px solid var(--line); }
.rank { color: var(--dim); font-variant-numeric: tabular-nums; font-size: 13px; padding-top: 2px; min-width: 16px; }
.body { flex: 1; min-width: 0; }
.cname { font-weight: 600; }
.unver { font-size: 11.5px; color: var(--warn); margin-left: 6px; }
.why { color: var(--dim); font-size: 13.5px; margin-top: 2px; }
.pick {
  font: inherit; font-size: 12.5px; padding: 4px 11px; border-radius: 6px; cursor: pointer;
  border: 1px solid var(--line); background: transparent; color: var(--dim); align-self: center; white-space: nowrap;
}
.pick:hover { border-color: var(--accent); color: var(--accent); }
.cand.chosen .pick { background: var(--ok); border-color: var(--ok); color: #fff; }
.cand.chosen .cname { color: var(--ok); }
.foot { display: flex; gap: 8px; margin-top: 11px; align-items: center; flex-wrap: wrap; }
.verdict { font-size: 13px; color: var(--ok); flex: 1; }
.link { font: inherit; font-size: 12.5px; background: none; border: none; color: var(--dim); cursor: pointer; text-decoration: underline; padding: 0; }
.empty { text-align: center; color: var(--dim); padding: 60px 20px; }
.others { font-size: 12.5px; color: var(--warn); margin-top: 5px; }
.answer { font-size: 21px; font-weight: 650; letter-spacing: -0.02em; margin: 6px 0 4px; }
.why.big { font-size: 14.5px; color: var(--ink); opacity: .82; }
details { margin-top: 11px; }
summary { cursor: pointer; font-size: 13px; color: var(--dim); }
summary:hover { color: var(--accent); }
.alt { padding: 9px 0 0 14px; border-left: 2px solid var(--line); margin-top: 9px; }
select {
  font: inherit; font-size: 14px; padding: 7px 10px; border-radius: 7px;
  border: 1px solid var(--line); background: var(--panel); color: var(--ink); cursor: pointer;
}
.gate {
  position: fixed; inset: 0; z-index: 50; background: var(--bg);
  display: flex; align-items: center; justify-content: center; padding: 20px;
}
.gate[hidden] { display: none; }
.gatebox { max-width: 460px; }
.gatebox h2 { font-size: 22px; margin: 0 0 12px; letter-spacing: -0.02em; }
.gatebox p { color: var(--dim); margin: 0 0 12px; }
.gatebox .fine { font-size: 13px; }
.gatebox input { width: 100%; margin: 8px 0 10px; padding: 10px 12px; }
.gatebox button {
  font: inherit; font-weight: 550; padding: 10px 18px; border-radius: 8px;
  border: none; background: var(--ink); color: var(--bg); cursor: pointer;
}
#gateerr { color: var(--accent); min-height: 18px; }
</style>
</head>
<body>
<div id="gate" class="gate" hidden>
  <div class="gatebox">
    <h2>Before you grade — who are you?</h2>
    <p>So we can tell whose picks are whose. First name is plenty.</p>
    <p class="fine">If you and someone else disagree, that's useful information,
    not a problem. Please don't compare notes first.</p>
    <input type="text" id="who" placeholder="Your name" autocomplete="name">
    <button id="start">Start grading</button>
    <button class="link" id="cancel">never mind</button>
    <p class="fine" id="gateerr"></p>
  </div>
</div>

<header><div class="bar">
  <h1>Elsewhere</h1>
  <select id="pairsel" title="Which city pair"></select>
  <input type="search" id="q" class="grow" placeholder="Search a place — try HEB, Torchy's, Barton Springs…">
  <div class="chips" id="filters">
    <button class="chip" data-f="all" aria-pressed="true">All</button>
    <button class="chip" data-f="todo" aria-pressed="false">Ungraded</button>
    <button class="chip" data-f="done" aria-pressed="false">Mine</button>
  </div>
  <button class="chip" id="modebtn" aria-pressed="false">Grade these</button>
  <div class="progress" id="prog"></div>
  <button class="link" id="signout"></button>
</div></header>
<main id="list"></main>
<script>
let S = null, filter = "all", q = "", grading = false, pending = null;
let me = localStorage.getItem("elsewhere.reviewer") || "";
// Remembered so a friend who picked their own city isn't reset to Austin's
// default every time they open the link.
let pair = JSON.parse(localStorage.getItem("elsewhere.pair") || "null")
           || { source: "", target: "" };

// Browsing is the default and needs no identity. A name is only asked for at
// the moment someone actually grades something — the gate used to sit in
// front of everything, which made a lookup tool read as a survey.
function askWho(after) {
  pending = after;
  document.getElementById("gate").hidden = false;
  document.getElementById("who").focus();
}

document.getElementById("start").addEventListener("click", () => {
  const v = document.getElementById("who").value.trim();
  if (v.length < 2) {
    document.getElementById("gateerr").textContent = "A name or nickname is enough.";
    return;
  }
  me = v;
  localStorage.setItem("elsewhere.reviewer", me);
  document.getElementById("gate").hidden = true;
  const go = pending; pending = null;
  load().then(() => { if (go) go(); });
});
document.getElementById("cancel").addEventListener("click", () => {
  document.getElementById("gate").hidden = true; pending = null;
  if (!me) setMode(false);
});
document.getElementById("who").addEventListener("keydown", e => {
  if (e.key === "Enter") document.getElementById("start").click();
});
document.getElementById("signout").addEventListener("click", () => {
  localStorage.removeItem("elsewhere.reviewer"); me = ""; load();
});

function setMode(on) {
  grading = on;
  document.getElementById("modebtn").setAttribute("aria-pressed", String(on));
  document.getElementById("modebtn").textContent = on ? "Done grading" : "Grade these";
  document.getElementById("filters").style.display = on ? "flex" : "none";
  if (!on && filter !== "all") {
    filter = "all";
    document.querySelectorAll(".chip[data-f]").forEach(x =>
      x.setAttribute("aria-pressed", String(x.dataset.f === "all")));
  }
  render();
}

document.getElementById("modebtn").addEventListener("click", () => {
  if (!grading && !me) { askWho(() => setMode(true)); return; }
  setMode(!grading);
});

const title = s => s.charAt(0).toUpperCase() + s.slice(1);

async function load() {
  const qs = new URLSearchParams({ reviewer: me, source: pair.source, target: pair.target });
  S = await (await fetch("/api/state?" + qs)).json();
  pair = { source: S.source, target: S.target };
  localStorage.setItem("elsewhere.pair", JSON.stringify(pair));

  const sel = document.getElementById("pairsel");
  sel.innerHTML = S.pairs.map(p =>
    `<option value="${p.source}|${p.target}" ${
      p.source === S.source && p.target === S.target ? "selected" : ""
    }>${title(p.source)} → ${title(p.target)}</option>`).join("");
  sel.style.display = S.pairs.length > 1 ? "" : "none";

  document.getElementById("signout").textContent = me ? "not " + me + "?" : "";
  render();
}

document.getElementById("pairsel").addEventListener("change", e => {
  const [source, target] = e.target.value.split("|");
  pair = { source, target };
  q = ""; document.getElementById("q").value = "";
  load();
});

function progress() {
  const el = document.getElementById("prog");
  if (!grading) {
    el.innerHTML = `<span style="opacity:.75">${S.matches.length} places</span>`;
    return;
  }
  const n = S.judged, t = S.threshold;
  const pct = Math.min(100, n / t * 100);
  const people = Object.keys(S.reviewers || {}).length;
  const msg = n >= t
    ? `<b>${n}</b> graded — enough for a real score`
    : `<b>${n}</b>/${t} graded`;
  const who = people > 1 ? ` <span style="opacity:.7">· ${people} people</span>` : "";
  el.innerHTML = msg + who + `<span class="track"><span class="fill" style="width:${pct}%"></span></span>`;
}

function visible(m) {
  if (filter === "todo" && m.mine) return false;
  if (filter === "done" && !m.mine) return false;
  if (filter === "unverified" && !m.candidates.some(c => c.verified === false)) return false;
  if (!q) return true;
  const hay = (m.name + " " + m.roles.join(" ") + " " +
               m.candidates.map(c => c.name + " " + c.reasoning).join(" ")).toLowerCase();
  return hay.includes(q);
}

function esc(s) {
  return String(s).replace(/[&<>"]/g, c => ({ "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;" }[c]));
}

function render() {
  if (!S) return;  // setMode() runs before the first load resolves
  progress();
  const rows = S.matches.filter(visible);
  const el = document.getElementById("list");
  if (!rows.length) { el.innerHTML = '<p class="empty">Nothing matches that.</p>'; return; }

  el.innerHTML = rows.map((m, idx) => {
    const q = s => esc(s).replace(/'/g, "\\'");
    const top = m.candidates[0];
    const rest = m.candidates.slice(1);

    if (!grading) {
      // Reading mode: lead with the answer. The alternates are there for
      // anyone who wants them, not in the way of everyone who doesn't.
      return `<div class="card">
        <div class="head"><h2>${esc(m.name)}</h2><span class="arrow">in ${esc(S.target)} is</span></div>
        <div class="answer">${esc(top.name)}</div>
        <div class="why big">${esc(top.reasoning)}</div>
        ${rest.length ? `<details><summary>${rest.length} other candidate${
          rest.length > 1 ? "s" : ""}</summary>${rest.map(c =>
          `<div class="alt"><span class="cname">${esc(c.name)}</span>
             <div class="why">${esc(c.reasoning)}</div></div>`).join("")}</details>` : ""}
      </div>`;
    }

    const cands = m.candidates.map((c, i) => {
      const isPick = m.mine && m.mine.toLowerCase() === c.name.toLowerCase();
      return `<div class="cand ${isPick ? "chosen" : ""}">
        <div class="rank">${i + 1}</div>
        <div class="body">
          <div><span class="cname">${esc(c.name)}</span>${
            c.verified === false ? '<span class="unver">unverified</span>' : ""}</div>
          <div class="why">${esc(c.reasoning)}</div>
        </div>
        <button class="pick" onclick="pick('${q(m.name)}', '${q(c.name)}')">${
          isPick ? "✓ yours" : "This one"}</button>
      </div>`;
    }).join("");

    // Showing what others picked turns a silent disagreement into a visible
    // one — which is the interesting case, not a conflict to hide.
    const others = m.others && m.others.length
      ? `<div class="others">others picked: ${m.others.map(esc).join(", ")}</div>` : "";

    return `<div class="card ${m.mine ? "done" : ""}">
      <div class="head"><h2>${esc(m.name)}</h2><span class="arrow">→ ${esc(S.target)}</span></div>
      <div class="meta">${m.roles.map(r => `<span class="role">${esc(r)}</span>`).join("")}
        · tier ${m.price_tier} · ${esc(m.reach)}</div>
      ${cands}
      ${others}
      <div class="foot">
        ${m.mine
          ? `<span class="verdict">✓ you picked ${esc(m.mine)}</span>
             <button class="link" onclick="clearPick('${q(m.name)}')">undo</button>`
          : `<input type="text" placeholder="or type a better answer…" class="grow"
               onkeydown="if(event.key==='Enter'&&this.value.trim())pick('${
                 q(m.name)}', this.value.trim(), true)">`}
      </div>
    </div>`;
  }).join("");
}

async function pick(name, answer, custom) {
  if (!me) { askWho(() => pick(name, answer, custom)); return; }
  const m = S.matches.find(x => x.name === name);
  const r = await fetch("/api/review", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      reviewer: me, source_name: name, source_city: S.source,
      target_city: S.target, answer: answer, custom: !!custom
    })
  });
  const j = await r.json();
  if (!j.ok) { alert(j.error || "couldn't save"); return; }
  m.mine = answer;
  m.others = (m.others || []).filter(a => a !== answer);
  S.judged = j.judged;
  render();
}

async function clearPick(name) {
  const r = await fetch("/api/review?reviewer=" + encodeURIComponent(me) +
                        "&source_name=" + encodeURIComponent(name) +
                        "&target_city=" + encodeURIComponent(S.target), { method: "DELETE" });
  const j = await r.json();
  S.matches.find(x => x.name === name).mine = null;
  S.judged = j.judged;
  render();
}

document.getElementById("q").addEventListener("input", e => {
  q = e.target.value.toLowerCase().trim(); render();
});
document.querySelectorAll(".chip").forEach(b => b.addEventListener("click", () => {
  filter = b.dataset.f;
  document.querySelectorAll(".chip").forEach(x =>
    x.setAttribute("aria-pressed", String(x === b)));
  render();
}));

setMode(false);
load();
</script>
</body>
</html>
"""
