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
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

from elsewhere import generate, links, verify


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
    # The whole corpus for a city is one 437 KB response, and it's every
    # answer the page will ever need — including the ones the landing
    # animation shows. Compressed it's a fifth of that, so the animation
    # costs no extra request and no extra round trip.
    app.add_middleware(GZipMiddleware, minimum_size=1024)

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

    # Snapshot of websites and coordinates, extracted offline. Absent in a
    # checkout that hasn't run `elsewhere links`, which only costs the
    # website buttons — map links are derived from the name.
    link_index = links.load()

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
                row.setdefault("links", links.for_place(link_index, m.source.name, src))
                row["cities"][tgt] = {
                    "candidates": [
                        {
                            "name": c.name,
                            "reasoning": c.reasoning,
                            "links": links.for_place(link_index, c.name, tgt),
                        }
                        for c in m.candidates
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
/* Palette: bubblegum-pink / golden-pollen / emerald / ocean-blue / dark-teal.
   No borders anywhere — separation comes from soft shadow, hierarchy from
   type size. Ocean-blue carries interaction in light and emerald in dark, so
   the accent always has contrast against its own ground; pink and gold are
   used sparingly, where a moment should feel like a moment. */
:root {
  color-scheme: light;
  --paper:  #F6FBFC;
  --panel:  #FFFFFF;
  --ink:    #073B4C;   /* dark-teal */
  --dim:    #4A6C78;
  --faint:  #8CA7B1;
  --hair:   #E0EEF2;
  --pink:   #EF476F;   /* bubblegum-pink */
  --gold:   #FFD166;   /* golden-pollen */
  --emerald:#06D6A0;
  --accent: #118AB2;   /* ocean-blue */
  --accent-deep: #0B6E8F;
  --accent-soft: #DFF1F8;
  --on-accent: #FFFFFF;
  --chip:   #E4F2F8;
  /* The wash at the top of the page: pollen and emerald, low and off-centre. */
  --wash-a: #FFE9B4;
  --wash-b: #C6F5E6;
  --shadow: 0 1px 2px rgba(7,59,76,.05), 0 12px 32px -16px rgba(7,59,76,.22);
  --lift:   0 2px 6px rgba(7,59,76,.08), 0 22px 50px -20px rgba(7,59,76,.32);
  --spring: cubic-bezier(.34, 1.4, .5, 1);
}
/* Opt-in only. Dark-teal ground, emerald accent — the same product at night. */
:root[data-theme="dark"] {
  color-scheme: dark;
  --paper: #073B4C; --panel: #0C4C61; --ink: #EFFAFC; --dim: #A7C5CF;
  --faint: #6F93A1; --hair: #145A70;
  --pink: #FF6B8B; --gold: #FFD166; --emerald: #06D6A0;
  --accent: #06D6A0; --accent-deep: #06D6A0; --accent-soft: #0E5A6E;
  --on-accent: #073B4C; --chip: #0E5A6E;
  --wash-a: #0C5163; --wash-b: #0A4553;
  --shadow: 0 1px 2px rgba(0,0,0,.35), 0 12px 32px -16px rgba(0,0,0,.6);
  --lift:   0 2px 6px rgba(0,0,0,.4), 0 22px 50px -20px rgba(0,0,0,.7);
}
* { box-sizing: border-box; }
body {
  margin: 0; color: var(--ink); background: var(--paper);
  background-image:
    radial-gradient(900px 340px at 18% -140px, var(--wash-a), transparent 68%),
    radial-gradient(760px 300px at 82% -120px, var(--wash-b), transparent 66%);
  background-repeat: no-repeat;
  font: 16px/1.62 ui-sans-serif, -apple-system, "Segoe UI", Inter, Roboto, sans-serif;
  -webkit-font-smoothing: antialiased; text-rendering: optimizeLegibility;
}

/* ─── Header ──────────────────────────────────────────────────────────────
   Full bleed rather than pinned to the 820px reading column: the brand and
   the theme toggle belong at the edges of the screen, not floating in from
   them. The content below stays in its column. */
/* The header reads as its own surface. Translucent paper over paper left it
   ambiguous whether the bar was chrome or content — the hairline and the
   lifted panel colour settle that. */
header {
  position: sticky; top: 0; z-index: 40; padding: 14px 28px;
  background: color-mix(in srgb, var(--panel) 88%, var(--accent) 4%);
  backdrop-filter: saturate(1.4) blur(14px);
  -webkit-backdrop-filter: saturate(1.4) blur(14px);
  box-shadow: 0 1px 0 var(--hair), 0 6px 20px -18px rgba(7,59,76,.5);
}
.bar { display: flex; gap: 14px; align-items: center; width: 100%; }
#barslot { display: flex; gap: 10px; align-items: center; flex: 1; min-width: 0; }
/* A search field stretched across a 1500px header reads as a text area, not
   a search box. */
#barslot .field { max-width: 560px; }
#savedbtn { margin-left: auto; }
#theme { padding: 9px 12px; font-size: 15px; line-height: 1; }

/* ─── Controls (city + search) ────────────────────────────────────────────
   One instance, moved between the hero and the header rather than
   duplicated — two search boxes would mean two sources of truth for what
   the visitor typed. */
#controls { display: flex; gap: 10px; align-items: center; flex: 1; min-width: 0; }
input[type=search], input[type=text], select {
  font: inherit; font-size: 15px; padding: 11px 16px; border: 0;
  border-radius: 999px; background: var(--panel); color: var(--ink);
  box-shadow: var(--shadow); transition: box-shadow .2s, transform .2s var(--spring);
}
input[type=search] { min-width: 0; }
input:focus, select:focus { outline: none; box-shadow: var(--lift), 0 0 0 2.5px var(--accent-soft); }
input::placeholder { color: var(--faint); }
/* The native <select> renders its list with the OS's own chrome — square
   corners, system blue, system font — which is the one part of the page the
   site's styling can't reach. So the menu is ours. The underlying <select>
   stays in the DOM as the source of truth and for keyboard and screen
   reader support; it's just visually replaced. */
select.native { position: absolute; opacity: 0; pointer-events: none; width: 1px; height: 1px; }
.citypick { position: relative; }
.citybtn {
  display: inline-flex; align-items: center; gap: 9px;
  font-size: 15px; font-weight: 700; letter-spacing: -0.01em;
  padding: 11px 16px; background: var(--panel); color: var(--ink);
  box-shadow: var(--shadow);
}
.citybtn:hover { box-shadow: var(--lift); }
.citybtn .caret { font-size: 10px; color: var(--faint); transition: transform .25s var(--spring); }
.citybtn[aria-expanded=true] .caret { transform: rotate(180deg); }
.citymenu {
  position: absolute; top: calc(100% + 8px); left: 0; z-index: 60;
  min-width: 100%; padding: 7px; border-radius: 18px;
  background: var(--panel); box-shadow: var(--lift);
  display: flex; flex-direction: column; gap: 2px;
  transform-origin: top center;
  animation: menuin .18s var(--spring);
}
.citymenu[hidden] { display: none; }
@keyframes menuin { from { opacity: 0; transform: translateY(-6px) scale(.97); } }
.citymenu button {
  text-align: left; white-space: nowrap; padding: 10px 16px; border-radius: 12px;
  font-size: 15px; font-weight: 650; background: none; color: var(--ink);
}
.citymenu button:hover { background: var(--chip); }
.citymenu button[aria-selected=true] { background: var(--accent); color: var(--on-accent); }
.from { font-size: 14px; color: var(--dim); font-weight: 600; white-space: nowrap; }
.field { position: relative; display: flex; flex: 1; min-width: 0; }
.field input[type=search] { width: 100%; padding-right: 42px; }
.field .clear {
  position: absolute; right: 7px; top: 50%; transform: translateY(-50%);
  width: 28px; height: 28px; background: var(--chip); color: var(--accent-deep);
  font-size: 17px; line-height: 1; display: grid; place-items: center;
}
.field .clear[hidden] { display: none; }
.field .clear:hover { background: var(--accent); color: var(--on-accent); }

button {
  font: inherit; border: 0; border-radius: 999px; cursor: pointer;
  transition: transform .22s var(--spring), box-shadow .2s, color .2s, background .2s;
}
.chip { font-size: 13.5px; padding: 9px 15px; background: var(--panel); color: var(--dim); box-shadow: var(--shadow); }
.chip:hover { color: var(--accent); transform: translateY(-2px); }
.chip:active { transform: translateY(0) scale(.97); }
button.brand {
  background: none; padding: 0; font-size: 17px; font-weight: 800;
  letter-spacing: -0.02em; color: var(--ink);
}
button.brand:hover { color: var(--accent); }

/* ─── The index ───────────────────────────────────────────────────────────
   Naming a place is the whole product, so on the index it owns the screen:
   the controls sit in the middle of the viewport at full size and the
   header carries nothing but the brand and the theme toggle. */
.stage {
  min-height: calc(100vh - 74px);
  display: flex; flex-direction: column; justify-content: center;
  text-align: center; padding: 24px 24px 60px; gap: 4px;
}
.stage[hidden] { display: none; }
.stage .inner { width: 100%; max-width: 720px; margin: 0 auto; }
.pitch {
  font-size: 46px; line-height: 1.06; letter-spacing: -0.045em; font-weight: 800;
  margin: 0 0 30px;
}
.pitch em, .sub em {
  font-style: normal;
  background: linear-gradient(100deg, var(--accent), var(--pink));
  -webkit-background-clip: text; background-clip: text; color: transparent;
}
/* In the hero the controls are the hero: big type, generous target. */
.stage #controls { justify-content: center; flex: 0 1 auto; }
.stage #controls .citybtn { font-size: 18px; padding: 16px 22px; }
.stage #controls .field { flex: 1 1 380px; max-width: 460px; }
.stage #controls input[type=search] { font-size: 18px; padding: 16px 22px; }
/* The name gets its own line and the headline reserves the space for it.
   Otherwise every change to a longer or shorter name reflows the block, and
   because the stage is vertically centred that shoves the entire page up and
   down mid-animation. */
#heroplace { display: inline-block; min-width: 3ch; }
.pitch { min-height: calc(1.06em * 3); }
#heroart:empty { display: none; }
.prompt.demo #heroplace { color: var(--accent); }
/* The demo's answers. Present but quiet — the headline is doing the talking,
   and these are the evidence under it. */
.peek {
  display: flex; gap: 18px; justify-content: center; flex-wrap: wrap;
  min-height: 26px; margin-top: 18px;
  opacity: 0; transform: translateY(6px); transition: opacity .45s, transform .45s var(--spring);
}
.peek.in { opacity: 1; transform: none; }
.peekrow { font-size: 15px; color: var(--dim); }
.peekcity { color: var(--faint); font-size: 12px; text-transform: uppercase;
  letter-spacing: .07em; font-weight: 700; margin-right: 7px; }
.peekrow b { color: var(--ink); font-weight: 700; }
.stage .q { font-size: 17px; font-weight: 650; color: var(--dim); margin: 34px 0 0; }

/* ─── Try rail ────────────────────────────────────────────────────────────
   The examples scroll on their own. Four static chips read as the whole
   catalogue; a moving rail reads as a sample of something larger, and shows
   more names than fit on one line. Hover or keyboard focus stops it so a
   chip can actually be clicked. */
.rail {
  overflow: hidden; margin-top: 14px;
  -webkit-mask-image: linear-gradient(90deg, transparent, #000 9%, #000 91%, transparent);
  mask-image: linear-gradient(90deg, transparent, #000 9%, #000 91%, transparent);
}
/* Stepped, not drifting: it rests on a name long enough to read, then moves
   in one quick beat. A constant crawl means every name is always slightly in
   motion and none of them is ever the one being offered. */
.track {
  display: flex; gap: 9px; width: max-content;
  transition: transform .55s var(--spring);
}
.rail.hold .track { transition: none; }   /* the seamless wrap, unanimated */
.eg {
  font-size: 14px; font-weight: 600; padding: 9px 16px; white-space: nowrap;
  background: var(--chip); color: var(--accent-deep);
}
.eg:nth-child(3n+2) { background: color-mix(in srgb, var(--gold) 34%, var(--panel)); color: var(--ink); }
.eg:nth-child(3n+3) { background: color-mix(in srgb, var(--emerald) 22%, var(--panel)); color: var(--ink); }
.eg:hover { background: var(--accent); color: var(--on-accent); transform: translateY(-2px); }

.or {
  font-size: 13px; color: var(--faint); margin: 34px 0 14px;
  text-transform: uppercase; letter-spacing: .09em; font-weight: 700;
}
.cats { display: flex; gap: 8px; justify-content: center; flex-wrap: wrap; }
.cats button {
  font-size: 14px; font-weight: 650; padding: 10px 18px;
  background: var(--panel); color: var(--dim); box-shadow: var(--shadow);
}
.cats button:hover { color: var(--accent); transform: translateY(-2px); box-shadow: var(--lift); }
.cats button .n { color: var(--faint); font-weight: 500; margin-left: 6px; font-size: 13px; }

/* ─── Results ─────────────────────────────────────────────────────────── */
main { max-width: 820px; margin: 0 auto; padding: 10px 24px 100px; }
.crumb {
  max-width: 820px; margin: 0 auto; padding: 22px 24px 4px;
  display: flex; align-items: baseline; gap: 12px;
}
.crumb[hidden] { display: none; }
.crumb span { font-size: 15px; font-weight: 650; color: var(--dim); }
.card {
  background: var(--panel); border-radius: 24px; padding: 30px 34px;
  margin-bottom: 18px; box-shadow: var(--shadow);
  transition: box-shadow .28s, transform .28s var(--spring);
}
.card:hover { box-shadow: var(--lift); transform: translateY(-3px); }
.head { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
.head h2 { font-size: 14.5px; margin: 0; font-weight: 650; color: var(--dim); letter-spacing: -0.01em; }
.arrow { color: var(--faint); font-size: 13.5px; font-style: italic; }
/* One block per city inside a place's card. A hairline between them, so the
   card still reads as one place rather than several. */
.city { padding-top: 20px; margin-top: 20px; box-shadow: inset 0 1px 0 var(--hair); }
.city:first-of-type { padding-top: 6px; margin-top: 6px; box-shadow: none; }
.cityname {
  font-size: 11.5px; font-weight: 700; letter-spacing: .07em; text-transform: uppercase;
  color: var(--accent); margin-bottom: 2px;
}
.answer {
  font-size: 34px; font-weight: 800; letter-spacing: -0.045em;
  line-height: 1.08; margin: 7px 0 12px;
}
/* Actions under an answer: where to go, and whether you're keeping it. */
.acts { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-top: 16px; }
.acts a, .acts .save {
  font: inherit; font-size: 13px; font-weight: 650; padding: 8px 14px;
  border-radius: 999px; background: var(--chip); color: var(--accent-deep);
  text-decoration: none; display: inline-flex; align-items: center; gap: 6px;
  transition: background .2s, color .2s, transform .22s var(--spring);
}
.acts a:hover, .acts .save:hover {
  background: var(--accent); color: var(--on-accent); transform: translateY(-2px);
}
/* A save is yours, not the site's — pink marks it as the one thing on the
   page you put there. */
.acts .save[aria-pressed=true] { background: var(--pink); color: #fff; }
.acts .save[aria-pressed=true]:hover { background: var(--pink); }
.acts .out { font-size: 11px; opacity: .6; }
.savecount { font-size: 12px; color: var(--faint); font-weight: 600; margin-left: 4px; }
#savedbtn[hidden] { display: none; }

.why { color: var(--dim); font-size: 14.5px; margin-top: 4px; max-width: 62ch; }
.why.big { font-size: 15.5px; color: var(--ink); opacity: .74; max-width: 62ch; }
.cname { font-weight: 700; font-size: 17px; letter-spacing: -0.02em; }
.alt { padding: 14px 0 0 18px; box-shadow: inset 2px 0 0 var(--hair); margin-top: 14px; }
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
  background: var(--chip); color: var(--accent-deep); font-size: 14px; line-height: 1;
  transition: transform .25s var(--spring);
}
details[open] summary::before { content: "\2013"; transform: rotate(180deg); }
summary:hover { color: var(--accent); }
.link {
  font: inherit; font-size: 13px; background: none; color: var(--faint);
  cursor: pointer; text-decoration: underline; text-underline-offset: 3px;
  padding: 0; transition: color .2s;
}
.link:hover { color: var(--accent); }
.empty { text-align: center; color: var(--faint); padding: 90px 20px; font-size: 17px; }

/* ─── First visit: choose a city ──────────────────────────────────────── */
.pick { padding: 76px 24px 40px; text-align: center; }
.pick[hidden] { display: none; }
.pick .wrap { max-width: 700px; margin: 0 auto; }
.brand { font-size: 15px; font-weight: 800; letter-spacing: -0.02em; color: var(--dim); margin-bottom: 26px; }
.sub { font-size: 17px; color: var(--dim); max-width: 30em; margin: 0 auto 44px; line-height: 1.55; }
.pick .q { font-size: 15px; font-weight: 650; color: var(--dim); margin: 0 0 16px; }
.cities { display: flex; gap: 12px; justify-content: center; flex-wrap: wrap; }
.cities button {
  font-size: 19px; font-weight: 700; letter-spacing: -0.02em;
  padding: 20px 34px; border-radius: 20px;
  background: var(--panel); color: var(--ink); box-shadow: var(--shadow);
}
.cities button:hover { transform: translateY(-3px); box-shadow: var(--lift); color: var(--accent); }
.cities button:active { transform: translateY(0) scale(.97); }

#app[hidden] { display: none; }

@media (max-width: 640px) {
  header { padding: 12px 16px; }
  .bar { gap: 10px; }
  .from { display: none; }        /* "I know" is implied by the city control */
  main { padding: 8px 16px 70px; }
  .crumb { padding: 16px 18px 2px; }
  .card { padding: 24px 22px; border-radius: 20px; }
  .answer { font-size: 27px; }
  .pitch { font-size: 31px; min-height: calc(1.06em * 4); }
  .sub { font-size: 16px; margin-bottom: 32px; }
  .cities button { font-size: 17px; padding: 16px 24px; flex: 1 1 40%; }
  .stage { padding: 16px 18px 48px; min-height: calc(100vh - 66px); }
  .stage #controls { flex-wrap: wrap; }
  .stage #controls .field { flex: 1 1 100%; }
}
@media (prefers-reduced-motion: reduce) {
  * { transition: none !important; animation: none !important; }
  .card:hover, .chip:hover, .cats button:hover, .eg:hover { transform: none; }
  /* Without the animation the rail would sit frozen showing three names,
     the second copy unreachable. Let it wrap instead. */
  .track { flex-wrap: wrap; justify-content: center; width: auto; }
  .rail { -webkit-mask-image: none; mask-image: none; }
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
    <h1 class="pitch">Every city has an H-E-B.<br><em>It's just elsewhere.</em></h1>
    <p class="sub">Name a place you love and we'll find its counterpart
    somewhere else — not the same category, the same <em>role</em>.</p>
    <p class="q">Which city do you know best?</p>
    <div class="cities" id="cities"></div>
  </div>
</section>

<div id="app" hidden>
  <header id="bar"><div class="bar">
    <button class="brand" id="homebtn" title="Start over">Elsewhere</button>
    <!-- #controls lives here on results pages and in the hero on the index;
         the same element either way. -->
    <div id="barslot"></div>
    <button class="chip" id="savedbtn" hidden title="Places you saved"></button>
    <button class="chip" id="theme" title="Switch theme" aria-label="Switch theme">☼</button>
  </div></header>

  <div id="controls">
    <span class="from">I know</span>
    <div class="citypick">
      <select id="srcsel" class="native" title="Which city you know" tabindex="-1"></select>
      <button class="citybtn" id="citybtn" aria-haspopup="listbox" aria-expanded="false"></button>
      <div class="citymenu" id="citymenu" role="listbox" hidden></div>
    </div>
    <div class="field">
      <input type="search" id="q" placeholder="Name a place you love…">
      <button class="clear" id="clearq" hidden aria-label="Clear">×</button>
    </div>
  </div>

  <!-- The index. Shown until they search or browse; dumping every card made
       the page read as a list to scroll rather than a box to type in. -->
  <section class="stage" id="prompt">
    <div class="inner">
      <h1 class="pitch">Every city has <span id="heroart">an</span><br><span id="heroplace">H-E-B</span>.<br><em>It\'s just elsewhere.</em></h1>
      <div id="heroslot"></div>
      <div class="peek" id="peek"></div>
      <p class="q" id="promptq"></p>
      <div class="rail"><div class="track" id="examples"></div></div>
      <p class="or">or browse</p>
      <div class="cats" id="cats"></div>
    </div>
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

/* ── Saved places ───────────────────────────────────────────────────────
   Local only, on purpose: there are no accounts, so a save that needed a
   server would be a save nobody could get back to. Keyed by city + name
   because the same name can exist in two cities. */
const SAVED_KEY = "elsewhere.saved";
const savedKey = (city, name) => city + "\u0000" + name;

function loadSaved() {
  try { return new Map(JSON.parse(localStorage.getItem(SAVED_KEY) || "[]")); }
  catch { return new Map(); }   // corrupt storage shouldn't take the page down
}
let saved = loadSaved();

function toggleSave(city, name, from, links) {
  const k = savedKey(city, name);
  if (saved.has(k)) saved.delete(k);
  else saved.set(k, { city, name, from, links, at: Date.now() });
  localStorage.setItem(SAVED_KEY, JSON.stringify([...saved]));
  renderSavedBtn();
}
const isSaved = (city, name) => saved.has(savedKey(city, name));

function renderSavedBtn() {
  const b = document.getElementById("savedbtn");
  b.hidden = saved.size === 0 && cat !== "__saved";
  b.innerHTML = `\u2605 Saved<span class="savecount">${saved.size}</span>`;
  document.querySelectorAll(".save").forEach(el => {
    const on = isSaved(el.dataset.city, el.dataset.name);
    el.setAttribute("aria-pressed", on);
    el.firstChild.textContent = on ? "\u2605 Saved" : "\u2606 Save";
  });
}

/* ── Deep links ────────────────────────────────────────────────────────
   The whole point is a link people pass around, so a result has to be
   linkable. Without this you can only tell someone "go here, then type
   Torchy's". */
function syncURL(replace) {
  const p = new URLSearchParams();
  if (home) p.set("city", home);
  if (q) p.set("q", q);
  else if (cat === "__saved") p.set("saved", "1");
  else if (cat) p.set("in", cat);
  const url = p.toString() ? "?" + p : location.pathname;
  history[replace ? "replaceState" : "pushState"]({ home, q, cat }, "", url);
}

function readURL() {
  const p = new URLSearchParams(location.search);
  return {
    city: p.get("city") || "",
    q: p.get("q") || "",
    cat: p.get("saved") ? "__saved" : (p.get("in") || ""),
  };
}

addEventListener("popstate", () => {
  const u = readURL();
  q = u.q; cat = u.cat;
  document.getElementById("q").value = q;
  if (u.city && u.city !== home) { home = u.city; load(); } else { render(); }
});

/* Links out, plus the save control. `rel=noopener` matters even for links we
   built ourselves — a website comes from upstream data, not from us. */
function acts(place, city, from) {
  const l = place.links || {};
  const site = l.website
    ? `<a href="${esc(l.website)}" target="_blank" rel="noopener noreferrer">Website<span class="out">\u2197</span></a>`
    : "";
  const map = l.map
    ? `<a href="${esc(l.map)}" target="_blank" rel="noopener noreferrer">Map &amp; reviews<span class="out">\u2197</span></a>`
    : "";
  return `<div class="acts">${site}${map}
    <button class="save" data-city="${esc(city)}" data-name="${esc(place.name)}"
      data-from="${esc(from || "")}" aria-pressed="false"><span>\u2606 Save</span></button>
  </div>`;
}

/* ── Rendering ─────────────────────────────────────────────────────── */
function render() {
  if (!S) return;

  const idle = !q && !cat;
  if (!idle) stopDemo();
  // The search box is the index; on results pages it retreats to the header.
  const slot = document.getElementById(idle ? "heroslot" : "barslot");
  const controls = document.getElementById("controls");
  if (controls.parentElement !== slot) slot.appendChild(controls);
  document.getElementById("prompt").hidden = !idle;
  document.getElementById("crumb").hidden = idle || !!q;
  document.getElementById("clearq").hidden = !q;
  if (idle) { document.getElementById("list").innerHTML = ""; return; }

  if (cat === "__saved") { renderSaved(); return; }

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
        ${acts(top, t, m.name)}
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
  renderSavedBtn();
}

function renderSaved() {
  const el = document.getElementById("list");
  const rows = [...saved.values()].sort((a, b) => b.at - a.at);
  document.getElementById("crumbtext").textContent =
    `Saved · ${rows.length} place${rows.length === 1 ? "" : "s"}`;
  el.innerHTML = rows.length
    ? rows.map(r => `<div class="card">
        <div class="head"><h2>${esc(r.from || "Saved")}</h2>
          <span class="arrow">${r.from ? "your answer for" : "saved"}</span></div>
        <div class="city">
          <div class="cityname">in ${esc(title(r.city))}</div>
          <div class="answer">${esc(r.name)}</div>
          ${acts(r, r.city, r.from)}
        </div>
      </div>`).join("")
    : `<p class="empty">Nothing saved yet. Star an answer to keep it here.</p>`;
  renderSavedBtn();
}

/* ── The try rail ──────────────────────────────────────────────────────
   Advances one chip at a time by measuring the real chip widths, so it lands
   flush regardless of how long a name is. When it reaches the end of the
   first copy it jumps back to the start with the transition off, which is
   invisible because the second copy is identical. */
let railAt = 0, railTimer = null;

function stepRail() {
  const track = document.getElementById("examples");
  const rail = track.parentElement;
  const chips = [...track.children];
  const half = chips.length / 2;
  if (!half) return;

  railAt++;
  if (railAt >= half) {
    railAt = 0;
    rail.classList.add("hold");
    track.style.transform = "translateX(0)";
    // Two frames: one for the style to land, one for the class to lift.
    requestAnimationFrame(() => requestAnimationFrame(() => rail.classList.remove("hold")));
    return;
  }
  const gap = 9;
  const shift = chips.slice(0, railAt).reduce((n, c) => n + c.offsetWidth + gap, 0);
  track.style.transform = `translateX(-${shift}px)`;
}

function startRail() {
  clearInterval(railTimer);
  if (matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  railTimer = setInterval(stepRail, 2600);
}

/* ── Landing demo ──────────────────────────────────────────────────────
   The page explains itself by doing the thing: a few well-known places from
   the chosen city cycle through the headline and the search box, with their
   real answers underneath. Every one of these already came down in the
   /api/state payload, so the whole sequence costs no extra request.

   It stops for good at the first sign of a real visitor — focus, a
   keystroke, a click — because an animation that keeps going while someone
   is typing is a bug, not a flourish. */
let demoOn = false, scrambleTimer = null;

/* "a" or "an", by sound rather than spelling.

   H-E-B is the case that makes this necessary: it starts with a consonant
   letter and takes "an", because what you say is "aitch". So initialisms are
   judged on the letter's name, everything else on its first vowel. Names
   that already begin with an article or "The" take none at all. */
const VOWEL_LETTERS = new Set(["A", "E", "F", "H", "I", "L", "M", "N", "O", "R", "S", "X"]);

function article(name) {
  const first = name.split(/[\s-]/)[0];
  if (/^(a|an)$/i.test(first)) return "";
  if (first.length === 1 || /^[A-Z]-[A-Z]/.test(name)) {
    return VOWEL_LETTERS.has(first[0].toUpperCase()) ? "an" : "a";
  }
  return /^[aeiou]/i.test(name) ? "an" : "a";
}

/* Letter-by-letter resolve, as a travelling wave.

   The whole tail churning at once is noise — you can't read it, so nothing
   is being *replaced*, the word just vanishes and a new one arrives. Instead
   a narrow band of scrambling characters moves left to right: ahead of it the
   old name is still legible, behind it the new one has landed. The band
   starts one character wide and widens as it travels, so the effect begins as
   a flicker and builds rather than exploding on frame one.

   Both names stay visible during the transition, which is the point — you can
   see the Austin place turning into the Chicago one. */
const SCRAMBLE = "ABCDEFGHIJKLMNOPQRSTUVWXYZ&'-";
const randChar = () => SCRAMBLE[(Math.random() * SCRAMBLE.length) | 0];

function scrambleTo(el, text) {
  clearInterval(scrambleTimer);
  const from = el.dataset.text || el.textContent || "";
  el.dataset.text = text;
  if (matchMedia("(prefers-reduced-motion: reduce)").matches) {
    el.textContent = text;
    return;
  }

  const span = Math.max(from.length, text.length);
  let head = 0;                       // leading edge of the wave
  scrambleTimer = setInterval(() => {
    head += 0.9;
    // One character wide at the start, six by the end.
    const band = 1 + (head / span) * 5;
    if (head - band > span) {
      el.textContent = text;
      clearInterval(scrambleTimer);
      return;
    }
    let out = "";
    for (let i = 0; i < span; i++) {
      if (i < head - band) out += text[i] ?? "";
      else if (i < head) out += (text[i] === " " || from[i] === " ") ? " " : randChar();
      else out += from[i] ?? "";
    }
    el.textContent = out;
  }, 38);
}

function demoPicks() {
  const known = new Map(S.matches.map(m => [m.name, m]));
  return (SEEDS_BY_CITY[S.source] || []).filter(n => known.has(n)).map(n => known.get(n))
    .concat(S.matches.slice(0, 4)).slice(0, 4);
}

/* The cycle is a little performance: someone types a place, thinks for a
   beat, gets an answer, then clears the box and tries another. Written as an
   async sequence rather than nested timers — the timing reads in order, and
   one cancellation token unwinds it from any point.

   `demoRun` increments on every stop, so a sequence that was mid-await when
   the visitor touched the page notices and returns instead of writing into
   the box under their hands. */
let demoRun = 0;
const sleep = ms => new Promise(r => setTimeout(r, ms));

/* Human typing isn't metronomic, and a uniform interval is the thing that
   makes an effect read as a cursor animation rather than a person. */
const beat = base => base + Math.random() * base * 0.7;

async function typeInto(box, text, token) {
  for (let i = 1; i <= text.length; i++) {
    if (token !== demoRun) return false;
    box.value = text.slice(0, i);
    await sleep(beat(52));
  }
  return token === demoRun;
}

async function deleteFrom(box, token) {
  while (box.value.length) {
    if (token !== demoRun) return false;
    box.value = box.value.slice(0, -1);
    await sleep(beat(26));   // deleting is always faster than typing
  }
  return token === demoRun;
}

async function demoCycle(token) {
  const picks = demoPicks();
  if (!picks.length) return;
  const box = document.getElementById("q");
  const peek = document.getElementById("peek");

  // Land on the static sentence first; a headline already mid-flight on
  // arrival reads as a glitch rather than a demonstration.
  await sleep(2200);

  for (let n = 0; token === demoRun; n++) {
    const m = picks[n % picks.length];

    if (!await typeInto(box, m.name, token)) return;
    await sleep(900);                    // the pause before you hit enter
    if (token !== demoRun) return;

    const shown = m.name.replace(/^The\s+/i, "");
    document.getElementById("heroart").textContent = article(shown);
    scrambleTo(document.getElementById("heroplace"), shown);

    peek.innerHTML = S.targets.filter(t => m.cities[t]).map(t =>
      `<span class="peekrow"><span class="peekcity">in ${esc(title(t))}</span>
         <b>${esc(m.cities[t].candidates[0].name)}</b></span>`).join("");
    peek.classList.remove("in");
    requestAnimationFrame(() => peek.classList.add("in"));

    await sleep(3400);                   // long enough to read the answer
    if (token !== demoRun) return;
    peek.classList.remove("in");
    if (!await deleteFrom(box, token)) return;
    await sleep(500);
  }
}

function startDemo() {
  if (demoOn || q || cat) return;
  demoOn = true;
  document.getElementById("prompt").classList.add("demo");
  demoCycle(demoRun);
}

function stopDemo() {
  if (!demoOn) return;
  demoOn = false;
  demoRun++;                             // any in-flight sequence stands down
  clearInterval(scrambleTimer);
  document.getElementById("prompt").classList.remove("demo");
  const hp = document.getElementById("heroplace");
  document.getElementById("heroart").textContent = "an";
  hp.textContent = hp.dataset.text = "H-E-B";
  document.getElementById("peek").innerHTML = "";
  document.getElementById("peek").classList.remove("in");
  const box = document.getElementById("q");
  if (!q) box.value = "";
}

/* ── Flow ──────────────────────────────────────────────────────────── */
const SEEDS_BY_CITY = {
  austin:   ["H-E-B", "Torchy's Tacos", "Barton Springs Pool", "BookPeople"],
  chicago:  ["Mariano's", "Lou Malnati's", "The Green Mill", "Reckless Records"],
  portland: ["Powell's City of Books", "Stumptown Coffee Roasters", "Salt & Straw", "Forest Park"]
};

function renderExamples() {
  const known = new Set(S.matches.map(m => m.name));
  const curated = (SEEDS_BY_CITY[S.source] || []).filter(n => known.has(n));
  // Pad from the corpus, evenly spaced so the rail isn't ten restaurants.
  const rest = S.matches.map(m => m.name).filter(n => !curated.includes(n));
  const step = Math.max(1, Math.floor(rest.length / 10));
  const list = curated.concat(rest.filter((_, i) => i % step === 0)).slice(0, 12);

  // Twice: the animation translates by half the track, so the second copy is
  // already in place when the first scrolls out.
  const chips = list.map(n =>
    `<button class="eg" data-name="${esc(n)}">${esc(n)}</button>`).join("");
  document.getElementById("examples").innerHTML = chips + chips;
  document.getElementById("promptq").textContent = `What do you love in ${title(S.source)}?`;
}

async function load() {
  S = await (await fetch("/api/state?source=" + encodeURIComponent(home))).json();
  home = S.source;
  localStorage.setItem("elsewhere.home", home);
  document.getElementById("srcsel").innerHTML = Object.keys(S.sources).map(c =>
    `<option value="${c}" ${c === S.source ? "selected" : ""}>${title(c)}</option>`).join("");
  renderExamples();
  renderCityMenu();
  renderCats();
  render();
  renderSavedBtn();
  railAt = 0;
  document.getElementById("examples").style.transform = "translateX(0)";
  startRail();
  if (!q && !cat) startDemo();
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
  stopDemo();
  document.getElementById("q").value = q;
  syncURL(); render();
});
["focus", "keydown", "pointerdown"].forEach(evt =>
  document.getElementById("q").addEventListener(evt, stopDemo));
document.getElementById("cats").addEventListener("pointerdown", stopDemo);
document.getElementById("examples").addEventListener("pointerdown", stopDemo);

document.getElementById("q").addEventListener("input", e => {
  q = e.target.value.trim();
  if (q) cat = "";
  syncURL(true);   // replace, so typing doesn't fill the back stack
  render();
});
/* ── City menu ─────────────────────────────────────────────────────────
   Rendered from the <select>'s own options, so there is still exactly one
   list of cities and one selected value. */
function renderCityMenu() {
  const sel = document.getElementById("srcsel");
  document.getElementById("citybtn").innerHTML =
    `${esc(title(sel.value))}<span class="caret">\u25BC</span>`;
  document.getElementById("citymenu").innerHTML = [...sel.options].map(o =>
    `<button role="option" data-city="${esc(o.value)}"
       aria-selected="${o.value === sel.value}">${esc(o.textContent)}</button>`).join("");
}

function openCityMenu(open) {
  document.getElementById("citymenu").hidden = !open;
  document.getElementById("citybtn").setAttribute("aria-expanded", open);
}

document.getElementById("citybtn").addEventListener("click", e => {
  e.stopPropagation();
  openCityMenu(document.getElementById("citymenu").hidden);
});
document.getElementById("citymenu").addEventListener("click", e => {
  const b = e.target.closest("button[data-city]");
  if (!b) return;
  openCityMenu(false);
  const sel = document.getElementById("srcsel");
  if (b.dataset.city === sel.value) return;
  sel.value = b.dataset.city;
  sel.dispatchEvent(new Event("change"));
});
// Click-away and Escape, the two ways every other menu on the web closes.
addEventListener("click", () => openCityMenu(false));
addEventListener("keydown", e => { if (e.key === "Escape") openCityMenu(false); });

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

document.getElementById("list").addEventListener("click", e => {
  const b = e.target.closest(".save");
  if (!b) return;
  const row = saved.get(savedKey(b.dataset.city, b.dataset.name));
  toggleSave(b.dataset.city, b.dataset.name, b.dataset.from,
             row ? row.links : linksFor(b));
  if (cat === "__saved") renderSaved();
});

/* The save button sits next to the links it should keep, so read them off
   the DOM rather than threading the object through the markup. */
function linksFor(btn) {
  const out = {};
  btn.parentElement.querySelectorAll("a").forEach(a => {
    out[a.textContent.startsWith("Website") ? "website" : "map"] = a.href;
  });
  return out;
}

document.getElementById("savedbtn").addEventListener("click", () => {
  cat = "__saved"; q = "";
  document.getElementById("q").value = "";
  syncURL(); render();
  scrollTo({ top: 0, behavior: "smooth" });
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
