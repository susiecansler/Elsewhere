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

    #: Cities you can ask *from*, and for each, the cities we can answer in.
    #: Derived from what's on disk, so generating a new direction is the only
    #: step needed to make it appear.
    sources: dict[str, list[str]] = {}
    for a, b in pairs:
        sources.setdefault(a, []).append(b)
    for tgts in sources.values():
        tgts.sort()

    default_source = source if source in sources else next(iter(sources))

    def state(reviewer: str | None, src: str) -> dict[str, Any]:
        """One row per place, carrying its answer in *every* target city.

        Grouping by source place rather than by city pair is what makes the
        app answer "where do I go instead" — someone types a place they know
        and sees it rendered into each city at once, instead of picking a
        direction first.
        """
        targets = sources[src]
        mine = {t: (store.for_reviewer(reviewer, t) if reviewer else {}) for t in targets}
        agreed = {t: store.consensus(t) for t in targets}

        # Keyed by place name so the same place lines up across cities.
        rows: dict[str, dict[str, Any]] = {}
        for tgt in targets:
            for m in corpora[(src, tgt)]:
                row = rows.setdefault(
                    m.source.name,
                    {
                        "name": m.source.name,
                        "aliases": m.source.aliases,
                        "category": m.source.category,
                        "roles": m.role_tags,
                        "price_tier": int(m.price_tier),
                        "reach": m.reach.value,
                        "cities": {},
                    },
                )
                picked = mine[tgt].get(m.source.name)
                row["cities"][tgt] = {
                    "mine": picked,
                    # Everyone's answers, so a reviewer sees they're
                    # disagreeing with someone rather than judging blind.
                    "others": [
                        a
                        for a in agreed[tgt].get(m.source.name, {}).get("accepted", [])
                        if a != picked
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

        return {
            "source": src,
            "targets": targets,
            "sources": {k: v for k, v in sorted(sources.items())},
            "threshold": evaluate.MIN_INDEPENDENT,
            "judged": sum(len(agreed[t]) for t in targets),
            "reviewers": store.reviewers(),
            "matches": [rows[k] for k in sorted(rows)],
        }

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return PAGE

    @app.get("/api/state")
    def api_state(reviewer: str = "", source: str = "") -> JSONResponse:
        return JSONResponse(
            state(reviewer or None, source if source in sources else default_source)
        )

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
    def api_unreview(reviewer: str, source_name: str, target_city: str) -> JSONResponse:
        # Required, not defaulted: with several cities loaded, falling back to
        # a startup value would delete someone's answer for the wrong city.
        store.forget(reviewer, source_name, target_city)
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
/* Sunny, minimalist, playful.
   No borders anywhere — separation comes from soft warm shadow. Hierarchy
   comes from type size, not rules. The one indulgence is the sun wash at the
   top of the page and a springy hover, which is where the play lives. */
:root {
  color-scheme: light;
  --paper:  #FFFDF6;
  --sun:    #FFE9A8;
  --peach:  #FFD9C2;
  --panel:  #FFFFFF;
  --ink:    #1A1613;
  --dim:    #7A7168;
  --faint:  #ADA398;
  --hair:   #F5EDE0;
  --amber:  #E8801F;
  --amber-soft: #FFF2DF;
  --sky:    #2C7FB8;
  --ok:     #17904B;
  --ok-soft:#E8F6EC;
  --warn:   #B26A0A;
  --chip:   #FFF6E6;
  --shadow:  0 1px 2px rgba(160,120,50,.05), 0 12px 32px -16px rgba(160,120,50,.22);
  --lift:    0 2px 6px rgba(160,120,50,.07), 0 22px 50px -20px rgba(160,120,50,.32);
  --spring: cubic-bezier(.34, 1.4, .5, 1);
}
/* Opt-in only. Dim, never cold — the same product after sunset. */
:root[data-theme="dark"] {
    color-scheme: dark;
    --paper: #17140F; --sun: #2E2617; --peach: #2B2018; --panel: #211D17;
    --ink: #F7F1E6; --dim: #A8A094; --faint: #7B7367; --hair: #2F2920;
    --amber: #F5A93F; --amber-soft: #2E2416;
    --sky: #6BB6E0; --ok: #58C98A; --ok-soft: #1B2A20; --warn: #E2A65C;
    --chip: #292217;
    --shadow: 0 1px 2px rgba(0,0,0,.35), 0 12px 32px -16px rgba(0,0,0,.6);
    --lift:   0 2px 6px rgba(0,0,0,.4), 0 22px 50px -20px rgba(0,0,0,.7);
}
* { box-sizing: border-box; }
body {
  margin: 0; color: var(--ink); background: var(--paper);
  /* Two overlapping suns, low and off-centre. Warmth, not a banner. */
  background-image:
    radial-gradient(900px 340px at 18% -140px, var(--sun), transparent 68%),
    radial-gradient(760px 300px at 82% -120px, var(--peach), transparent 66%);
  background-repeat: no-repeat;
  font: 16px/1.62 ui-sans-serif, -apple-system, "Segoe UI", Inter, Roboto, sans-serif;
  -webkit-font-smoothing: antialiased; text-rendering: optimizeLegibility;
}
header {
  position: sticky; top: 0; z-index: 20; padding: 18px 24px 16px;
  background: color-mix(in srgb, var(--paper) 78%, transparent);
  backdrop-filter: saturate(1.6) blur(14px);
  -webkit-backdrop-filter: saturate(1.6) blur(14px);
}
.bar { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; max-width: 820px; margin: 0 auto; }
h1 {
  font-size: 20px; margin: 0 6px 0 0; font-weight: 800; letter-spacing: -0.04em;
  background: linear-gradient(96deg, var(--ink) 20%, var(--amber));
  -webkit-background-clip: text; background-clip: text; color: transparent;
}
input[type=search], input[type=text], select {
  font: inherit; font-size: 15px; padding: 11px 16px; border: 0;
  border-radius: 999px; background: var(--panel); color: var(--ink);
  box-shadow: var(--shadow); transition: box-shadow .2s, transform .2s var(--spring);
}
input[type=search] { min-width: 200px; }
input:focus, select:focus { outline: none; box-shadow: var(--lift), 0 0 0 2.5px var(--amber-soft); }
input::placeholder { color: var(--faint); }
select { cursor: pointer; font-weight: 650; padding-right: 38px; letter-spacing: -0.01em; }
.grow { flex: 1; }
.progress { font-size: 13.5px; color: var(--dim); white-space: nowrap; }
.progress b { color: var(--ink); font-weight: 700; }
.track {
  width: 110px; height: 6px; background: var(--chip); border-radius: 99px;
  overflow: hidden; display: inline-block; vertical-align: middle; margin-left: 10px;
}
.fill { height: 100%; border-radius: 99px; background: linear-gradient(90deg, var(--sun), var(--amber)); transition: width .45s var(--spring); }
.chips { display: flex; gap: 6px; }
.chip, .pick, .gatebox button {
  font: inherit; font-weight: 650; border: 0; cursor: pointer;
  border-radius: 999px; transition: transform .22s var(--spring), box-shadow .2s, background .2s, color .2s;
}
.chip { font-size: 13.5px; padding: 9px 15px; background: var(--panel); color: var(--dim); box-shadow: var(--shadow); }
.chip:hover { color: var(--amber); transform: translateY(-2px); }
.chip:active { transform: translateY(0) scale(.97); }
.chip[aria-pressed=true] { background: var(--ink); color: var(--paper); box-shadow: var(--lift); }
#theme { padding: 9px 12px; font-size: 15px; line-height: 1; }
#modebtn[aria-pressed=false] { background: linear-gradient(135deg, var(--sun), var(--peach)); color: var(--warn); }
main { max-width: 820px; margin: 0 auto; padding: 10px 24px 100px; }

.card {
  background: var(--panel); border-radius: 24px; padding: 30px 34px;
  margin-bottom: 18px; box-shadow: var(--shadow);
  transition: box-shadow .28s, transform .28s var(--spring);
}
.card:hover { box-shadow: var(--lift); transform: translateY(-3px); }
.card.done { box-shadow: var(--shadow), inset 4px 0 0 var(--ok); }

/* "Alamo Drafthouse Cinema  in chicago is"  —  reads as a sentence, so the
   answer below it lands as the punchline. */
.head { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
.head h2 { font-size: 14.5px; margin: 0; font-weight: 650; color: var(--dim); letter-spacing: -0.01em; }
.arrow { color: var(--faint); font-size: 13.5px; font-style: italic; }
.meta { font-size: 12.5px; color: var(--faint); margin: 12px 0 16px; }
.role {
  display: inline-block; background: var(--chip); color: var(--warn);
  padding: 4px 11px; border-radius: 999px; margin: 0 5px 5px 0;
  font-size: 11.5px; font-weight: 650; letter-spacing: .01em;
}
.answer {
  font-size: 34px; font-weight: 800; letter-spacing: -0.045em;
  line-height: 1.08; margin: 7px 0 12px;
}
.why.big { font-size: 15.5px; color: var(--ink); opacity: .74; max-width: 62ch; }
details { margin-top: 18px; }
summary {
  cursor: pointer; font-size: 13.5px; color: var(--faint); font-weight: 650;
  list-style: none; display: inline-flex; align-items: center; gap: 7px;
  transition: color .2s;
}
summary::-webkit-details-marker { display: none; }
summary::before {
  content: "+"; display: grid; place-items: center;
  width: 20px; height: 20px; border-radius: 999px;
  background: var(--chip); color: var(--warn); font-size: 14px; line-height: 1;
  transition: transform .25s var(--spring);
}
details[open] summary::before { content: "–"; transform: rotate(180deg); }
summary:hover { color: var(--amber); }
.alt { padding: 14px 0 0 18px; box-shadow: inset 2px 0 0 var(--hair); margin-top: 14px; }
.from { font-size: 14px; color: var(--dim); font-weight: 600; }
/* One block per city inside a place's card. A hairline between them, so the
   card still reads as one place rather than several. */
.city { padding-top: 20px; margin-top: 20px; box-shadow: inset 0 1px 0 var(--hair); }
.city:first-of-type { padding-top: 6px; margin-top: 6px; box-shadow: none; }
.city.done { box-shadow: inset 0 1px 0 var(--hair), inset 3px 0 0 var(--ok); padding-left: 16px; }
.cityname {
  font-size: 11.5px; font-weight: 700; letter-spacing: .07em; text-transform: uppercase;
  color: var(--amber); margin-bottom: 2px;
}

/* Grading */
.cand { display: flex; gap: 15px; padding: 16px 0; box-shadow: inset 0 1px 0 var(--hair); }
.cand:first-of-type { box-shadow: none; }
.rank {
  flex: none; width: 24px; height: 24px; border-radius: 999px; margin-top: 2px;
  display: grid; place-items: center; background: var(--chip); color: var(--warn);
  font-size: 12px; font-weight: 700; font-variant-numeric: tabular-nums;
}
.body { flex: 1; min-width: 0; }
.cname { font-weight: 700; font-size: 17px; letter-spacing: -0.02em; }
.unver { font-size: 11.5px; color: var(--warn); margin-left: 8px; font-weight: 600; }
.why { color: var(--dim); font-size: 14.5px; margin-top: 4px; max-width: 62ch; }
.pick {
  font-size: 13px; padding: 9px 17px; background: var(--chip); color: var(--warn);
  align-self: center; white-space: nowrap;
}
.pick:hover { background: var(--amber); color: #fff; transform: translateY(-2px); }
.pick:active { transform: scale(.96); }
.cand.chosen .rank { background: var(--ok-soft); color: var(--ok); }
.cand.chosen .pick { background: var(--ok); color: #fff; }
.cand.chosen .cname { color: var(--ok); }
.foot { display: flex; gap: 10px; margin-top: 16px; align-items: center; flex-wrap: wrap; }
.verdict { font-size: 13.5px; color: var(--ok); font-weight: 650; flex: 1; }
.link {
  font: inherit; font-size: 13px; background: none; border: 0; color: var(--faint);
  cursor: pointer; text-decoration: underline; text-underline-offset: 3px;
  padding: 0; transition: color .2s;
}
.link:hover { color: var(--amber); }
.others { font-size: 13px; color: var(--warn); margin-top: 10px; }
.empty { text-align: center; color: var(--faint); padding: 90px 20px; font-size: 17px; }

/* Name prompt */
.gate {
  position: fixed; inset: 0; z-index: 50; padding: 24px;
  background: color-mix(in srgb, var(--paper) 74%, transparent);
  backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px);
  display: flex; align-items: center; justify-content: center;
}
.gate[hidden] { display: none; }
.gatebox {
  max-width: 430px; background: var(--panel); padding: 38px 40px;
  border-radius: 28px; box-shadow: var(--lift);
  animation: pop .34s var(--spring);
}
@keyframes pop { from { opacity: 0; transform: translateY(10px) scale(.97); } }
.gatebox h2 { font-size: 27px; margin: 0 0 14px; letter-spacing: -0.04em; line-height: 1.15; font-weight: 800; }
.gatebox p { color: var(--dim); margin: 0 0 14px; }
.gatebox .fine { font-size: 14px; color: var(--faint); }
.gatebox input { width: 100%; margin: 12px 0 16px; }
.gatebox button { font-size: 15px; padding: 13px 24px; background: var(--ink); color: var(--paper); }
.gatebox button:hover { transform: translateY(-2px); box-shadow: var(--lift); }
.gatebox button:active { transform: scale(.97); }
/* Must out-specify `.gatebox button`, or the secondary action reads as
   a second primary button and the choice stops being obvious. */
.gatebox button.link {
  margin-left: 16px; background: none; color: var(--faint); font-weight: 500;
  padding: 0; box-shadow: none; border-radius: 0;
}
.gatebox button.link:hover { color: var(--amber); transform: none; box-shadow: none; }
#gateerr { color: var(--amber); min-height: 18px; margin: 10px 0 0; font-size: 13.5px; font-weight: 550; }

@media (max-width: 640px) {
  header { padding: 12px 16px; }
  main { padding: 8px 16px 70px; }
  .card { padding: 24px 22px; border-radius: 20px; }
  .answer { font-size: 27px; }
  h1 { font-size: 18px; }
}
@media (prefers-reduced-motion: reduce) {
  * { transition: none !important; animation: none !important; }
  .card:hover, .chip:hover, .pick:hover { transform: none; }
}
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
  <span class="from">I know</span>
  <select id="srcsel" title="Which city you know"></select>
  <input type="search" id="q" class="grow" placeholder="Search a place…">
  <div class="chips" id="filters">
    <button class="chip" data-f="all" aria-pressed="true">All</button>
    <button class="chip" data-f="todo" aria-pressed="false">Ungraded</button>
    <button class="chip" data-f="done" aria-pressed="false">Mine</button>
  </div>
  <button class="chip" id="modebtn" aria-pressed="false">Grade these</button>
  <div class="progress" id="prog"></div>
  <button class="chip" id="theme" title="Switch theme" aria-label="Switch theme">☀</button>
  <button class="link" id="signout"></button>
</div></header>
<main id="list"></main>
<script>
let S = null, filter = "all", q = "", grading = false, pending = null;
let me = localStorage.getItem("elsewhere.reviewer") || "";
// The city the visitor actually knows. Remembered so a Portland friend
// isn't reset to Austin every time they open the link.
let home = localStorage.getItem("elsewhere.home") || "";

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

function applyTheme(t) {
  document.documentElement.setAttribute("data-theme", t);
  document.getElementById("theme").textContent = t === "dark" ? "☾" : "☀";
  localStorage.setItem("elsewhere.theme", t);
}
document.getElementById("theme").addEventListener("click", () => {
  applyTheme(document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark");
});
applyTheme(localStorage.getItem("elsewhere.theme") || "light");

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
  const qs = new URLSearchParams({ reviewer: me, source: home });
  S = await (await fetch("/api/state?" + qs)).json();
  home = S.source;
  localStorage.setItem("elsewhere.home", home);

  const sel = document.getElementById("srcsel");
  sel.innerHTML = Object.keys(S.sources).map(c =>
    `<option value="${c}" ${c === S.source ? "selected" : ""}>${title(c)}</option>`).join("");

  document.getElementById("signout").textContent = me ? "not " + me + "?" : "";
  render();
}

document.getElementById("srcsel").addEventListener("change", e => {
  home = e.target.value;
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

// Mirrors places.normalize on the server: fold punctuation so the way people
// type a name doesn't have to match how it's printed.
function norm(s) {
  return s.toLowerCase()
          .replace(/[‘’'`]/g, "")
          .replace(/[^a-z0-9]+/g, " ")
          .trim();
}

// How well a place answers the query. 0 means it doesn't.
//   3  the name or a curated alias starts with what was typed
//   2  the name or an alias contains it
//   1  it only turns up in a role or in some city's answer
// Ranking matters because searching "torchys" otherwise surfaces every card
// whose reasoning happens to mention Torchy's before Torchy's itself.
function score(m, needle) {
  const cities = Object.values(m.cities);
  const names = [m.name, ...(m.aliases || [])];
  // Compare with spaces removed so "heb" reaches "H-E-B", but only against
  // names — doing it to prose makes "the bar" match "heb".
  const tight = needle.replace(/ /g, "");
  for (const n of names) {
    const k = norm(n), kt = k.replace(/ /g, "");
    if (k.startsWith(needle) || kt.startsWith(tight)) return 3;
    if (k.includes(needle) || kt.includes(tight)) return 2;
  }
  const body = norm(
    m.roles.join(" ") + " " +
    cities.flatMap(c => c.candidates.map(x => x.name + " " + x.reasoning)).join(" ")
  );
  return body.includes(needle) ? 1 : 0;
}

function visible(m) {
  const cities = Object.values(m.cities);
  // "Graded" means every city was judged, not just one — a place with a
  // Chicago answer and no Portland answer is still unfinished.
  const anyMine = cities.some(c => c.mine);
  const allMine = cities.length > 0 && cities.every(c => c.mine);
  if (filter === "todo" && allMine) return false;
  if (filter === "done" && !anyMine) return false;
  if (!q) return true;
  return score(m, norm(q)) > 0;
}

function esc(s) {
  return String(s).replace(/[&<>"]/g, c => ({ "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;" }[c]));
}

function render() {
  if (!S) return;  // setMode() runs before the first load resolves
  progress();
  let rows = S.matches.filter(visible);
  if (q) {
    const needle = norm(q);
    rows = rows.map(m => [score(m, needle), m])
               .sort((a, b) => b[0] - a[0] || a[1].name.localeCompare(b[1].name))
               .map(([, m]) => m);
  }
  const el = document.getElementById("list");
  if (!rows.length) { el.innerHTML = '<p class="empty">Nothing matches that.</p>'; return; }

  el.innerHTML = rows.map(m => {
    const q = s => esc(s).replace(/'/g, "\\'");

    // One place, its answer in every city we can answer for. Grouping this
    // way is the point: you type somewhere you know and see it rendered
    // into each city at once, rather than choosing a direction first.
    const cities = S.targets.filter(t => m.cities[t]);

    if (!grading) {
      const blocks = cities.map(t => {
        const c = m.cities[t];
        const top = c.candidates[0];
        const rest = c.candidates.slice(1);
        return `<div class="city">
          <div class="cityname">in ${esc(title(t))}</div>
          <div class="answer">${esc(top.name)}</div>
          <div class="why big">${esc(top.reasoning)}</div>
          ${rest.length ? `<details><summary>${rest.length} other option${
            rest.length > 1 ? "s" : ""}</summary>${rest.map(x =>
            `<div class="alt"><span class="cname">${esc(x.name)}</span>
               <div class="why">${esc(x.reasoning)}</div></div>`).join("")}</details>` : ""}
        </div>`;
      }).join("");

      return `<div class="card">
        <div class="head"><h2>${esc(m.name)}</h2>
          <span class="arrow">${esc(title(S.source))}</span></div>
        ${blocks}
      </div>`;
    }

    // Grading: every city is judged separately, because "the Chicago one is
    // right but the Portland one is nonsense" is a normal and useful verdict.
    const graded = cities.some(t => m.cities[t].mine);
    const blocks = cities.map(t => {
      const c = m.cities[t];
      const cands = c.candidates.map((x, i) => {
        const isPick = c.mine && c.mine.toLowerCase() === x.name.toLowerCase();
        return `<div class="cand ${isPick ? "chosen" : ""}">
          <div class="rank">${i + 1}</div>
          <div class="body">
            <div><span class="cname">${esc(x.name)}</span>${
              x.verified === false ? '<span class="unver">unverified</span>' : ""}</div>
            <div class="why">${esc(x.reasoning)}</div>
          </div>
          <button class="pick" onclick="pick('${q(m.name)}','${q(t)}','${q(x.name)}')">${
            isPick ? "✓ yours" : "This one"}</button>
        </div>`;
      }).join("");

      const others = c.others && c.others.length
        ? `<div class="others">others picked: ${c.others.map(esc).join(", ")}</div>` : "";

      return `<div class="city ${c.mine ? "done" : ""}">
        <div class="cityname">in ${esc(title(t))}</div>
        ${cands}
        ${others}
        <div class="foot">
          ${c.mine
            ? `<span class="verdict">✓ you picked ${esc(c.mine)}</span>
               <button class="link" onclick="clearPick('${q(m.name)}','${q(t)}')">undo</button>`
            : `<input type="text" placeholder="or type a better answer…" class="grow"
                 onkeydown="if(event.key==='Enter'&&this.value.trim())pick('${
                   q(m.name)}','${q(t)}',this.value.trim(),true)">`}
        </div>
      </div>`;
    }).join("");

    return `<div class="card ${graded ? "done" : ""}">
      <div class="head"><h2>${esc(m.name)}</h2>
        <span class="arrow">${esc(title(S.source))}</span></div>
      <div class="meta">${m.roles.map(r => `<span class="role">${esc(r)}</span>`).join("")}
        · tier ${m.price_tier} · ${esc(m.reach)}</div>
      ${blocks}
    </div>`;
  }).join("");
}

async function pick(name, city, answer, custom) {
  if (!me) { askWho(() => pick(name, city, answer, custom)); return; }
  const m = S.matches.find(x => x.name === name);
  const r = await fetch("/api/review", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      reviewer: me, source_name: name, source_city: S.source,
      target_city: city, answer: answer, custom: !!custom
    })
  });
  const j = await r.json();
  if (!j.ok) { alert(j.error || "couldn't save"); return; }
  m.cities[city].mine = answer;
  m.cities[city].others = (m.cities[city].others || []).filter(a => a !== answer);
  S.judged = j.judged;
  render();
}

async function clearPick(name, city) {
  const r = await fetch("/api/review?reviewer=" + encodeURIComponent(me) +
                        "&source_name=" + encodeURIComponent(name) +
                        "&target_city=" + encodeURIComponent(city), { method: "DELETE" });
  const j = await r.json();
  S.matches.find(x => x.name === name).cities[city].mine = null;
  S.judged = j.judged;
  render();
}

document.getElementById("q").addEventListener("input", e => {
  q = e.target.value.trim(); render();
});
// Scoped to [data-f]: a bare `.chip` selector also caught the theme and
// mode buttons, so clicking either set filter to undefined and flipped
// their aria-pressed, inverting their colours.
document.querySelectorAll(".chip[data-f]").forEach(b => b.addEventListener("click", () => {
  filter = b.dataset.f;
  document.querySelectorAll(".chip[data-f]").forEach(x =>
    x.setAttribute("aria-pressed", String(x === b)));
  render();
}));

setMode(false);
load();
</script>
</body>
</html>
"""
