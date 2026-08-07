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
from pydantic import BaseModel

from elsewhere import evaluate, generate, verify


def load_corpus(source: str, target: str) -> list:
    """Prefer the verified corpus; fall back to raw."""
    path = verify.verified_path(source, target)
    if not path.exists():
        path = generate.raw_path(source, target)
    return generate.read_matches(path)


class ReviewIn(BaseModel):
    source_name: str
    source_city: str
    target_city: str
    accepted: list[str]
    note: str | None = None


def create_app(source: str = "austin", target: str = "chicago") -> FastAPI:
    app = FastAPI(title="Elsewhere", docs_url=None, redoc_url=None)

    def state() -> dict[str, Any]:
        matches = load_corpus(source, target)
        truth = evaluate.load_ground_truth()
        judged = {t.source_name: t.accepted for t in truth if t.provenance in evaluate.INDEPENDENT}
        independent = len(judged)

        return {
            "source": source,
            "target": target,
            "threshold": evaluate.MIN_INDEPENDENT,
            "independent": independent,
            "matches": [
                {
                    "name": m.source.name,
                    "category": m.source.category,
                    "roles": m.role_tags,
                    "price_tier": int(m.price_tier),
                    "reach": m.reach.value,
                    "reviewed": judged.get(m.source.name),
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
                for m in matches
            ],
        }

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return PAGE

    @app.get("/api/state")
    def api_state() -> JSONResponse:
        return JSONResponse(state())

    @app.post("/api/review")
    def api_review(body: ReviewIn) -> JSONResponse:
        truth = evaluate.load_ground_truth()
        # Replace any prior judgment for this place rather than appending a
        # second, conflicting one.
        truth = [
            t
            for t in truth
            if not (t.source_name == body.source_name and t.provenance in evaluate.INDEPENDENT)
        ]
        truth.append(
            evaluate.GroundTruth(
                source_name=body.source_name,
                source_city=body.source_city,
                target_city=body.target_city,
                accepted=body.accepted,
                provenance="reviewed",
                note=body.note,
            )
        )
        evaluate.write_ground_truth(truth)
        independent = len([t for t in truth if t.provenance in evaluate.INDEPENDENT])
        return JSONResponse({"ok": True, "independent": independent})

    @app.delete("/api/review/{source_name}")
    def api_unreview(source_name: str) -> JSONResponse:
        truth = evaluate.load_ground_truth()
        truth = [
            t
            for t in truth
            if not (t.source_name == source_name and t.provenance in evaluate.INDEPENDENT)
        ]
        evaluate.write_ground_truth(truth)
        independent = len([t for t in truth if t.provenance in evaluate.INDEPENDENT])
        return JSONResponse({"ok": True, "independent": independent})

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
</style>
</head>
<body>
<header><div class="bar">
  <h1>Elsewhere <span id="pair"></span></h1>
  <input type="search" id="q" class="grow" placeholder="Search a place, role, or answer…">
  <div class="chips">
    <button class="chip" data-f="all" aria-pressed="true">All</button>
    <button class="chip" data-f="todo" aria-pressed="false">To review</button>
    <button class="chip" data-f="done" aria-pressed="false">Reviewed</button>
    <button class="chip" data-f="unverified" aria-pressed="false">Unverified</button>
  </div>
  <div class="progress" id="prog"></div>
</div></header>
<main id="list"></main>
<script>
let S = null, filter = "all", q = "";

async function load() {
  S = await (await fetch("/api/state")).json();
  document.getElementById("pair").textContent = S.source + " → " + S.target;
  render();
}

function progress() {
  const n = S.independent, t = S.threshold;
  const pct = Math.min(100, n / t * 100);
  const msg = n >= t
    ? `<b>${n}</b> reviewed — enough for a real score`
    : `<b>${n}</b>/${t} reviewed`;
  document.getElementById("prog").innerHTML =
    msg + `<span class="track"><span class="fill" style="width:${pct}%"></span></span>`;
}

function visible(m) {
  if (filter === "todo" && m.reviewed) return false;
  if (filter === "done" && !m.reviewed) return false;
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
  progress();
  const rows = S.matches.filter(visible);
  const el = document.getElementById("list");
  if (!rows.length) { el.innerHTML = '<p class="empty">Nothing matches that.</p>'; return; }

  el.innerHTML = rows.map(m => {
    const chosen = m.reviewed || [];
    const cands = m.candidates.map((c, i) => {
      const isPick = chosen.some(a => a.toLowerCase() === c.name.toLowerCase());
      return `<div class="cand ${isPick ? "chosen" : ""}">
        <div class="rank">${i + 1}</div>
        <div class="body">
          <div><span class="cname">${esc(c.name)}</span>${
            c.verified === false ? '<span class="unver">unverified</span>' : ""}</div>
          <div class="why">${esc(c.reasoning)}</div>
        </div>
        <button class="pick" onclick="pick('${esc(m.name).replace(/'/g, "\\\\'")}', '${
          esc(c.name).replace(/'/g, "\\\\'")}')">${isPick ? "✓ chosen" : "This one"}</button>
      </div>`;
    }).join("");

    return `<div class="card ${m.reviewed ? "done" : ""}">
      <div class="head"><h2>${esc(m.name)}</h2><span class="arrow">→ ${esc(S.target)}</span></div>
      <div class="meta">${m.roles.map(r => `<span class="role">${esc(r)}</span>`).join("")}
        · tier ${m.price_tier} · ${esc(m.reach)}</div>
      ${cands}
      <div class="foot">
        ${m.reviewed
          ? `<span class="verdict">✓ you chose ${esc(m.reviewed.join(", "))}</span>
             <button class="link" onclick="clearPick('${esc(m.name).replace(/'/g, "\\\\'")}')">undo</button>`
          : `<input type="text" placeholder="or type a better answer…" class="grow"
               onkeydown="if(event.key==='Enter'&&this.value.trim())pick('${
                 esc(m.name).replace(/'/g, "\\\\'")}', this.value.trim(), true)">`}
      </div>
    </div>`;
  }).join("");
}

async function pick(name, answer, custom) {
  const m = S.matches.find(x => x.name === name);
  const r = await fetch("/api/review", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      source_name: name, source_city: S.source, target_city: S.target,
      accepted: [answer], note: custom ? "reviewer supplied a better answer" : null
    })
  });
  const j = await r.json();
  m.reviewed = [answer];
  S.independent = j.independent;
  render();
}

async function clearPick(name) {
  const r = await fetch("/api/review/" + encodeURIComponent(name), { method: "DELETE" });
  const j = await r.json();
  S.matches.find(x => x.name === name).reviewed = null;
  S.independent = j.independent;
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

load();
</script>
</body>
</html>
"""
