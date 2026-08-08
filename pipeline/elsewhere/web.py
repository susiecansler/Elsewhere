"""The Elsewhere web app.

A lookup tool: say which city you know, name a place you love, and see its
counterpart in the other cities — matched by the *role* a place plays in
local life, not by category.

Read-only by design. There are no write endpoints and nothing to log in to;
the corpus is generated offline by the pipeline and served as static data.
Evaluation and reviewing live in the CLI (`elsewhere review`, `elsewhere
eval`), deliberately not here.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

from elsewhere import generate, verify


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
    for suffix in (".raw.jsonl", ".verified.jsonl"):
        for path in generate.MATCHES_DIR.glob(f"*-*{suffix}"):
            stem = path.name.removesuffix(suffix)
            if "-" in stem:
                source, target = stem.split("-", 1)
                pairs.add((source, target))
    return sorted(pairs)


def create_app(source: str = "austin", target: str = "chicago") -> FastAPI:
    app = FastAPI(title="Elsewhere", docs_url=None, redoc_url=None)

    pairs = available_pairs() or [(source, target)]
    # Load every corpus once at startup. Together they're a few MB, and
    # re-reading per request would make switching cities feel sluggish.
    corpora = {p: load_corpus(*p) for p in pairs}

    #: Cities you can ask *from*, and for each, the cities we can answer in.
    sources: dict[str, list[str]] = {}
    for a, b in pairs:
        sources.setdefault(a, []).append(b)
    for tgts in sources.values():
        tgts.sort()

    default_source = source if source in sources else next(iter(sources))

    def state(src: str) -> dict[str, Any]:
        """One row per place, carrying its answer in *every* target city.

        Grouping by source place rather than by city pair is what makes the
        app answer "where do I go instead" — someone names a place they know
        and sees it rendered into each city at once.
        """
        targets = sources[src]

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
                        "cities": {},
                    },
                )
                row["cities"][tgt] = {
                    "candidates": [
                        {"name": c.name, "reasoning": c.reasoning} for c in m.candidates
                    ],
                }

        return {
            "source": src,
            "targets": targets,
            "sources": {k: v for k, v in sorted(sources.items())},
            "matches": [rows[k] for k in sorted(rows)],
        }

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return PAGE

    @app.get("/api/cities")
    def api_cities() -> JSONResponse:
        """Cities that can be used as a starting point."""
        return JSONResponse(sorted(sources))

    @app.get("/api/state")
    def api_state(source: str = "") -> JSONResponse:
        return JSONResponse(state(source if source in sources else default_source))

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
<title>Elsewhere — every city has an H-E-B</title>
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
.chips { display: flex; gap: 6px; }
/* Every button here is a pill. Without this reset the browser's default
   chrome (outset border, square corners) shows through — as it did on the
   theme toggle once the review buttons that used to carry it were removed. */
button {
  font: inherit; border: 0; border-radius: 999px; cursor: pointer;
  transition: transform .22s var(--spring), box-shadow .2s, color .2s, background .2s;
}
.chip, .pick { font-size: 13.5px; padding: 9px 15px; background: var(--panel); color: var(--dim); box-shadow: var(--shadow); }
.chip:hover { color: var(--amber); transform: translateY(-2px); }
.chip:active { transform: translateY(0) scale(.97); }
.chip[aria-pressed=true] { background: var(--ink); color: var(--paper); box-shadow: var(--lift); }
#theme { padding: 9px 12px; font-size: 15px; line-height: 1; }
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

.cname { font-weight: 700; font-size: 17px; letter-spacing: -0.02em; }
.why { color: var(--dim); font-size: 14.5px; margin-top: 4px; max-width: 62ch; }
.link {
  font: inherit; font-size: 13px; background: none; border: 0; color: var(--faint);
  cursor: pointer; text-decoration: underline; text-underline-offset: 3px;
  padding: 0; transition: color .2s;
}
.link:hover { color: var(--amber); }
.empty { text-align: center; color: var(--faint); padding: 90px 20px; font-size: 17px; }

@keyframes pop { from { opacity: 0; transform: translateY(10px) scale(.97); } }
/* Must out-specify `
@media (max-width: 640px) {
  header { padding: 12px 16px; }
  main { padding: 8px 16px 70px; }
  .card { padding: 24px 22px; border-radius: 20px; }
  .answer { font-size: 27px; }
  h1 { font-size: 18px; }
}
@media (prefers-reduced-motion: reduce) {
  * { transition: none !important; animation: none !important; }
  .card:hover, .chip:hover, .cats button:hover { transform: none; }
}
/* ─── Arrival ─────────────────────────────────────────────────────────── */
.hero { padding: 76px 24px 40px; text-align: center; }
.hero .wrap { max-width: 720px; margin: 0 auto; }
.brand {
  font-size: 15px; font-weight: 800; letter-spacing: -0.02em; color: var(--dim);
  margin-bottom: 26px;
}
.brand.small { margin: 0; font-size: 17px; color: var(--ink); }
.pitch {
  font-size: 46px; line-height: 1.06; letter-spacing: -0.045em; font-weight: 800;
  margin: 0 0 34px;
}
.pitch em {
  font-style: normal;
  background: linear-gradient(100deg, var(--amber), #E85D2A);
  -webkit-background-clip: text; background-clip: text; color: transparent;
}
.ask .lead { font-size: 16px; color: var(--dim); font-weight: 600; }
.ask select { font-size: 17px; padding: 13px 40px 13px 18px; font-weight: 700; }
.ask input[type=search] {
  font-size: 17px; padding: 13px 20px; min-width: 300px; flex: 0 1 340px;
}
.examples {
  display: flex; gap: 8px; justify-content: center; flex-wrap: wrap;
  margin-top: 22px; min-height: 34px;
}
.examples .eg {
  font: inherit; font-size: 13.5px; font-weight: 600; padding: 7px 14px;
  border: 0; border-radius: 999px; cursor: pointer;
  background: var(--chip); color: var(--warn);
  transition: transform .22s var(--spring), background .2s, color .2s;
}
.examples .eg:hover { background: var(--amber); color: #fff; transform: translateY(-2px); }
.examples .lead { font-size: 13.5px; color: var(--faint); align-self: center; margin-right: 2px; }

#bar { position: sticky; top: 0; z-index: 20; }

@media (max-width: 640px) {
  .hero { padding: 48px 18px 28px; }
  .pitch { font-size: 32px; }
  .ask input[type=search] { min-width: 0; flex: 1 1 100%; }
}
/* ─── First visit: choose a city ──────────────────────────────────────── */
.pick[hidden] { display: none; }
.pick .wrap { max-width: 700px; margin: 0 auto; }
.brand {
  font-size: 15px; font-weight: 800; letter-spacing: -0.02em; color: var(--dim);
  margin-bottom: 26px;
}
.brand.small { margin: 0; font-size: 16px; color: var(--ink); }
.pitch {
  font-size: 46px; line-height: 1.06; letter-spacing: -0.045em; font-weight: 800;
  margin: 0 0 22px;
}
.pitch em, .sub em {
  font-style: normal;
  background: linear-gradient(100deg, var(--amber), #E85D2A);
  -webkit-background-clip: text; background-clip: text; color: transparent;
}
.sub {
  font-size: 17px; color: var(--dim); max-width: 30em; margin: 0 auto 44px;
  line-height: 1.55;
}
.q { font-size: 15px; font-weight: 650; color: var(--dim); margin: 0 0 16px; }
.cities { display: flex; gap: 12px; justify-content: center; flex-wrap: wrap; }
.cities button {
  font: inherit; font-size: 19px; font-weight: 700; letter-spacing: -0.02em;
  padding: 20px 34px; border: 0; border-radius: 20px; cursor: pointer;
  background: var(--panel); color: var(--ink); box-shadow: var(--shadow);
  transition: transform .22s var(--spring), box-shadow .2s, color .2s;
}
.cities button:hover {
  transform: translateY(-3px); box-shadow: var(--lift); color: var(--amber);
}
.cities button:active { transform: translateY(0) scale(.97); }

/* ─── Prompt shown before they search ─────────────────────────────────── */
.prompt { text-align: center; padding: 64px 24px 20px; }
.prompt[hidden] { display: none; }
.prompt .q { font-size: 19px; color: var(--ink); font-weight: 700; letter-spacing: -0.02em; }
.examples {
  display: flex; gap: 8px; justify-content: center; flex-wrap: wrap; margin-top: 18px;
}
.examples .eg {
  font: inherit; font-size: 14px; font-weight: 600; padding: 9px 16px;
  border: 0; border-radius: 999px; cursor: pointer;
  background: var(--chip); color: var(--warn);
  transition: transform .22s var(--spring), background .2s, color .2s;
}
.examples .eg:hover { background: var(--amber); color: #fff; transform: translateY(-2px); }
.examples .lead { font-size: 14px; color: var(--faint); align-self: center; margin-right: 2px; }

#bar { position: sticky; top: 0; z-index: 20; }
#app[hidden] { display: none; }
.from { font-size: 14px; color: var(--dim); font-weight: 600; }

@media (max-width: 640px) {
    .pitch { font-size: 32px; }
  .sub { font-size: 16px; margin-bottom: 32px; }
  .cities button { font-size: 17px; padding: 16px 24px; flex: 1 1 40%; }
  .prompt { padding: 44px 18px 12px; }
}
/* ─── Nav additions ───────────────────────────────────────────────────── */
button.brand.small {
  background: none; border: 0; cursor: pointer; padding: 0;
  font: inherit; font-size: 16px; font-weight: 800; letter-spacing: -0.02em;
  color: var(--ink); transition: color .2s;
}
button.brand.small:hover { color: var(--amber); }

/* Search field with a clear affordance. Without one, getting back from a
   query to the browse view meant selecting the text and deleting it. */
.field { position: relative; display: flex; }
.field input[type=search] { width: 100%; padding-right: 40px; }
.field .clear {
  position: absolute; right: 6px; top: 50%; transform: translateY(-50%);
  width: 26px; height: 26px; border: 0; border-radius: 999px; cursor: pointer;
  background: var(--chip); color: var(--warn); font-size: 17px; line-height: 1;
  display: grid; place-items: center; transition: background .2s, color .2s;
}
.field .clear[hidden] { display: none; }
.field .clear:hover { background: var(--amber); color: #fff; }

.or {
  font-size: 13px; color: var(--faint); margin: 30px 0 14px;
  text-transform: uppercase; letter-spacing: .09em; font-weight: 700;
}
.cats { display: flex; gap: 8px; justify-content: center; flex-wrap: wrap; }
.cats button {
  font: inherit; font-size: 14px; font-weight: 650; padding: 10px 18px;
  border: 0; border-radius: 999px; cursor: pointer;
  background: var(--panel); color: var(--dim); box-shadow: var(--shadow);
  transition: transform .22s var(--spring), box-shadow .2s, color .2s;
}
.cats button:hover { color: var(--amber); transform: translateY(-2px); box-shadow: var(--lift); }
.cats button .n { color: var(--faint); font-weight: 500; margin-left: 6px; font-size: 13px; }

/* Tells you what you're looking at, and how to get out of it. */
.crumb {
  max-width: 820px; margin: 0 auto; padding: 22px 24px 4px;
  display: flex; align-items: baseline; gap: 12px;
}
.crumb[hidden] { display: none; }
.crumb span { font-size: 15px; font-weight: 650; color: var(--dim); }

@media (max-width: 640px) {
  .bar { gap: 8px; }
  .from { display: none; }        /* "I know" is implied by the city control */
  .crumb { padding: 16px 18px 2px; }
}
</style>
</head>
<body>
<!-- First visit: pick a city before anything else. A dropdown defaulted to
     Austin meant a Chicago visitor saw Austin places and had to notice a
     control they had no reason to look at. -->
<section id="pick" class="pick" hidden>
  <div class="wrap">
    <div class="brand">Elsewhere</div>
    <h1 class="pitch">Every city has an H-E-B.<br><em>It's just called something else.</em></h1>
    <p class="sub">Name a place you love and we'll find its counterpart
    somewhere else — not the same category, the same <em>role</em>.</p>
    <p class="q">Which city do you know best?</p>
    <div class="cities" id="cities"></div>
  </div>
</section>

<div id="app" hidden>
  <header id="bar"><div class="bar">
    <button class="brand small" id="homebtn" title="Start over">Elsewhere</button>
    <span class="from">I know</span>
    <select id="srcsel" title="Which city you know"></select>
    <div class="field grow">
      <input type="search" id="q" placeholder="Name a place you love…">
      <button class="clear" id="clearq" hidden aria-label="Clear">×</button>
    </div>
    <button class="chip" id="theme" title="Switch theme" aria-label="Switch theme">☀</button>
  </div></header>

  <!-- Shown until they search. Dumping every card made the page read as a
       list to scroll rather than a box to type in. -->
  <section class="prompt" id="prompt">
    <p class="q" id="promptq"></p>
    <div class="examples" id="examples"></div>
    <p class="or">or browse</p>
    <div class="cats" id="cats"></div>
  </section>

  <div class="crumb" id="crumb" hidden>
    <span id="crumbtext"></span>
    <button class="link" id="crumbclear">clear</button>
  </div>

  <main id="list"></main>
</div>

<script>
let S = null, q = "", cat = "";
let home = localStorage.getItem("elsewhere.home") || "";

const title = s => s.charAt(0).toUpperCase() + s.slice(1);
const esc = s => String(s).replace(/[&<>"]/g, c =>
  ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;" }[c]));

/* ── Theme ─────────────────────────────────────────────────────────────
   Light by default regardless of the visitor's OS: the design is a sunny
   one, and prefers-color-scheme would hand that choice to whichever
   machine opened the link. */
function applyTheme(t) {
  document.documentElement.setAttribute("data-theme", t);
  document.getElementById("theme").textContent = t === "dark" ? "☾" : "☀";
  localStorage.setItem("elsewhere.theme", t);
}
document.getElementById("theme").addEventListener("click", () =>
  applyTheme(document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark"));
applyTheme(localStorage.getItem("elsewhere.theme") || "light");

/* ── Search ────────────────────────────────────────────────────────────
   Mirrors places.normalize on the server: fold punctuation so the way
   people type a name doesn't have to match how it's printed. */
function norm(s) {
  return s.toLowerCase().replace(/[‘’'`]/g, "").replace(/[^a-z0-9]+/g, " ").trim();
}

/* How well a place answers the query. 0 means it doesn't.
     3  the name or a curated alias starts with what was typed
     2  the name or an alias contains it
     1  it only turns up in a role or in some city's answer
   Ranking matters because searching "torchys" otherwise surfaces every
   card whose reasoning happens to mention Torchy's before Torchy's. */
function score(m, needle) {
  const names = [m.name, ...(m.aliases || [])];
  // Compare with spaces removed so "heb" reaches "H-E-B", but only against
  // names — doing it to prose makes "the bar" match "heb".
  const tight = needle.replace(/ /g, "");
  for (const n of names) {
    const k = norm(n), kt = k.replace(/ /g, "");
    if (k.startsWith(needle) || kt.startsWith(tight)) return 3;
    if (k.includes(needle) || kt.includes(tight)) return 2;
  }
  const body = norm(m.roles.join(" ") + " " +
    Object.values(m.cities).flatMap(c =>
      c.candidates.map(x => x.name + " " + x.reasoning)).join(" "));
  return body.includes(needle) ? 1 : 0;
}

/* ── Browsing ──────────────────────────────────────────────────────────
   Search alone is a dead end for anyone who can't think of a place. The
   seed categories are too granular to show raw (44 of them), so they fold
   into a handful of buckets people actually think in. */
const GROUPS = [
  ["Food",          "Food",          ["restaurant", "dessert"]],
  ["Drinks",        "Bars & beer",   ["bar", "brewery"]],
  ["Coffee",        "Coffee",        ["coffee"]],
  ["Groceries",     "Groceries",     ["grocery", "convenience", "farmers"]],
  ["Shops",         "Shops",         ["retail", "bank"]],
  ["Outdoors",      "Outdoors",      ["outdoor", "park"]],
  ["Culture",       "Culture",       ["music", "museum", "theater", "cinema", "library", "landmark"]],
  ["Neighborhoods", "Neighborhoods", ["neighborhood"]],
  ["Fitness",       "Fitness",       ["gym"]],
];

const groupOf = m => {
  const head = (m.category || "").split("_")[0];
  const hit = GROUPS.find(([, , heads]) => heads.includes(head));
  return hit ? hit[0] : "Other";
};

function renderCats() {
  const counts = {};
  for (const m of S.matches) counts[groupOf(m)] = (counts[groupOf(m)] || 0) + 1;
  document.getElementById("cats").innerHTML = GROUPS
    .filter(([key]) => counts[key])
    .map(([key, label]) =>
      `<button data-cat="${key}">${label}<span class="n">${counts[key]}</span></button>`)
    .join("");
}

/* ── Deep links ────────────────────────────────────────────────────────
   The whole point is a link people pass around, so a result has to be
   linkable. Without this you can only tell someone "go here, then type
   Torchy's". */
function syncURL(replace) {
  const p = new URLSearchParams();
  if (home) p.set("city", home);
  if (q) p.set("q", q);
  else if (cat) p.set("in", cat);
  const url = p.toString() ? "?" + p : location.pathname;
  history[replace ? "replaceState" : "pushState"]({ home, q, cat }, "", url);
}

function readURL() {
  const p = new URLSearchParams(location.search);
  return { city: p.get("city") || "", q: p.get("q") || "", cat: p.get("in") || "" };
}

addEventListener("popstate", () => {
  const u = readURL();
  q = u.q; cat = u.cat;
  document.getElementById("q").value = q;
  if (u.city && u.city !== home) { home = u.city; load(); } else { render(); }
});

/* ── Rendering ─────────────────────────────────────────────────────── */
function render() {
  if (!S) return;

  const idle = !q && !cat;
  document.getElementById("prompt").hidden = !idle;
  document.getElementById("crumb").hidden = idle || !!q;
  document.getElementById("clearq").hidden = !q;
  if (idle) { document.getElementById("list").innerHTML = ""; return; }

  let rows;
  if (q) {
    const needle = norm(q);
    rows = S.matches
      .map(m => [score(m, needle), m])
      .filter(([s]) => s > 0)
      .sort((a, b) => b[0] - a[0] || a[1].name.localeCompare(b[1].name))
      .map(([, m]) => m);
  } else {
    rows = S.matches.filter(m => groupOf(m) === cat);
    const label = (GROUPS.find(([k]) => k === cat) || [cat, cat])[1];
    document.getElementById("crumbtext").textContent =
      `${label} in ${title(S.source)} · ${rows.length} places`;
  }

  const el = document.getElementById("list");
  if (!rows.length) {
    el.innerHTML = `<p class="empty">Nothing here called “${esc(q)}”. Try another place.</p>`;
    return;
  }

  el.innerHTML = rows.map(m => {
    // One place, its answer in every city we can answer for.
    const blocks = S.targets.filter(t => m.cities[t]).map(t => {
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
  }).join("");
}

/* ── Flow ──────────────────────────────────────────────────────────── */
const SEEDS_BY_CITY = {
  austin:   ["H-E-B", "Torchy's Tacos", "Barton Springs Pool", "BookPeople"],
  chicago:  ["Mariano's", "Lou Malnati's", "The Green Mill", "Reckless Records"],
  portland: ["Powell's City of Books", "Stumptown Coffee Roasters", "Salt & Straw", "Forest Park"]
};

function renderExamples() {
  const known = new Set(S.matches.map(m => m.name));
  const picks = (SEEDS_BY_CITY[S.source] || []).filter(n => known.has(n)).slice(0, 4);
  const list = picks.length ? picks : S.matches.slice(0, 4).map(m => m.name);
  document.getElementById("examples").innerHTML =
    `<span class="lead">try</span>` +
    list.map(n => `<button class="eg" data-name="${esc(n)}">${esc(n)}</button>`).join("");
  document.getElementById("promptq").textContent = `What do you love in ${title(S.source)}?`;
}

async function load() {
  S = await (await fetch("/api/state?source=" + encodeURIComponent(home))).json();
  home = S.source;
  localStorage.setItem("elsewhere.home", home);
  document.getElementById("srcsel").innerHTML = Object.keys(S.sources).map(c =>
    `<option value="${c}" ${c === S.source ? "selected" : ""}>${title(c)}</option>`).join("");
  renderExamples();
  renderCats();
  render();
}

async function boot() {
  // A shared link carries its own city and query, and must not be
  // overridden by whatever this browser happened to choose last time.
  const u = readURL();
  if (u.city) home = u.city;
  q = u.q; cat = u.cat;

  if (!home) {
    const cities = await (await fetch("/api/cities")).json();
    document.getElementById("cities").innerHTML =
      cities.map(c => `<button data-city="${c}">${title(c)}</button>`).join("");
    document.getElementById("pick").hidden = false;
    return;
  }
  document.getElementById("app").hidden = false;
  load().then(() => { document.getElementById("q").value = q; render(); });
}

function chooseCity(c) {
  home = c;
  localStorage.setItem("elsewhere.home", home);
  document.getElementById("pick").hidden = true;
  document.getElementById("app").hidden = false;
  syncURL();
  load().then(() => document.getElementById("q").focus());
}

/* Delegated rather than inline: an onclick built by interpolation breaks on
   any name containing an apostrophe — Lou Malnati's, Torchy's, Mariano's —
   and does so silently, because the attribute is a JS syntax error. */
document.getElementById("cities").addEventListener("click", e => {
  const b = e.target.closest("button[data-city]");
  if (b) chooseCity(b.dataset.city);
});
document.getElementById("examples").addEventListener("click", e => {
  const b = e.target.closest(".eg");
  if (!b) return;
  q = b.dataset.name; cat = "";
  document.getElementById("q").value = q;
  syncURL(); render();
});
document.getElementById("q").addEventListener("input", e => {
  q = e.target.value.trim();
  if (q) cat = "";
  syncURL(true);   // replace, so typing doesn't fill the back stack
  render();
});
document.getElementById("srcsel").addEventListener("change", e => {
  home = e.target.value;
  localStorage.setItem("elsewhere.home", home);
  q = ""; cat = "";
  document.getElementById("q").value = "";
  syncURL();
  load();
});

document.getElementById("cats").addEventListener("click", e => {
  const b = e.target.closest("button[data-cat]");
  if (!b) return;
  cat = b.dataset.cat; q = "";
  document.getElementById("q").value = "";
  syncURL(); render();
  document.getElementById("crumb").scrollIntoView({ behavior: "smooth", block: "start" });
});

function clearView() {
  q = ""; cat = "";
  document.getElementById("q").value = "";
  syncURL(); render();
  scrollTo({ top: 0, behavior: "smooth" });
}
document.getElementById("clearq").addEventListener("click", clearView);
document.getElementById("crumbclear").addEventListener("click", clearView);
document.getElementById("homebtn").addEventListener("click", clearView);

boot();
</script>
</body>
</html>
"""
