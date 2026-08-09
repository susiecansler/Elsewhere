"""The Elsewhere web app.

Stretch goal, not built: geolocate the visitor's city so the source picker
starts on where they actually are instead of on Austin. The corpus only
covers three cities, so today the guess would be wrong more often than
right — worth revisiting once there are enough cities for a nearest-match
to be meaningful.

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
/* One surface, everywhere. The yellow field broke the app in half — a
   poster for two screens and a document for the rest — so the whole thing is
   now a single warm white, and continuity comes for free. Colour arrives as
   accent and as the soft discs behind the hero, not as a ground that
   switches.

   Structure is carried by hairlines and radius rather than by blocks of
   colour: a white page, 1px rules, generous rounding, one hot accent. */
:root {
  color-scheme: light;
  --paper:  #FFFFFF;
  --sunk:   #F7F7F7;   /* the only other surface: inset wells, hovers */
  --panel:  #FFFFFF;
  --ink:    #1C1C1C;
  --dim:    #6A6A6A;
  --faint:  #9B9B9B;
  --hair:   #E4E4E4;
  --hair-2: #DDDDDD;
  --accent: #E0355A;          /* the one hot colour */
  --accent-deep: #C22B4C;
  --accent-soft: #FFF1F4;
  --on-accent: #FFFFFF;
  --pink: #E0355A; --pink-deep: #C22B4C; --on-pink: #FFFFFF;
  --turquoise: #067A6F;
  --gold: #FFC53D;
  --emerald: #16A97F;
  --chip: #F2F2F2;
  --bar: #FFFFFF; --bar-ink: #1C1C1C; --bar-line: #DDDDDD;
  --bar-dim: #7A7A7A; --bar-field: #FFFFFF;
  /* Soft, wide, and low — the shadow of something resting on the page, not
     floating above it. */
  --shadow: 0 1px 2px rgba(0,0,0,.05), 0 4px 12px rgba(0,0,0,.06);
  --lift:   0 2px 4px rgba(0,0,0,.07), 0 10px 28px rgba(0,0,0,.12);
  --hard:   var(--lift); --hard-sm: var(--shadow);
  --display: var(--sans);
  --sans: ui-sans-serif, -apple-system, "Segoe UI", Inter, Roboto, "Helvetica Neue", sans-serif;
  --mono: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  --spring: cubic-bezier(.2, .8, .3, 1);
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --paper: #121212; --sunk: #1D1D1D; --panel: #1A1A1A; --ink: #F4F4F4;
  --dim: #ADADAD; --faint: #7E7E7E; --hair: #2E2E2E; --hair-2: #383838;
  --accent: #FF5A78; --accent-deep: #FF7A92; --accent-soft: #2A1A1E;
  --on-accent: #1A1A1A;
  --pink: #FF5A78; --pink-deep: #FF7A92; --on-pink: #17171A;
  --turquoise: #37C9B8; --gold: #FFC53D; --emerald: #35C89B;
  --chip: #242424;
  --bar: #121212; --bar-ink: #F4F4F4; --bar-line: #303030;
  --bar-dim: #9A9A9A; --bar-field: #1D1D1D;
  --shadow: 0 1px 2px rgba(0,0,0,.5), 0 4px 12px rgba(0,0,0,.45);
  --lift:   0 2px 4px rgba(0,0,0,.55), 0 10px 28px rgba(0,0,0,.6);
}
* { box-sizing: border-box; }
body {
  margin: 0; color: var(--ink); background: var(--paper);
  font: 16px/1.6 var(--sans); letter-spacing: -0.003em;
  -webkit-font-smoothing: antialiased; text-rendering: optimizeLegibility;
}

/* ─── Header ──────────────────────────────────────────────────────────────
   Full bleed rather than pinned to the 820px reading column: the brand and
   the theme toggle belong at the edges of the screen, not floating in from
   them. The content below stays in its column. */
/* The header reads as its own surface. Translucent paper over paper left it
   ambiguous whether the bar was chrome or content — the hairline and the
   lifted panel colour settle that. */
/* A solid bar, dark in both themes. Chrome that matches the page reads as
   part of the page; chrome that doesn't reads as a frame around it, and a
   frame is what tells you this is a product rather than a document. */
/* White, sticky, and separated by a hairline rather than by contrast. The
   dark bar framed the page like an app chrome; this lets the page start at
   the top of the screen and keeps the eye on the content. */
header {
  position: sticky; top: 0; z-index: 40; padding: 0 40px;
  background: var(--bar); color: var(--bar-ink);
  box-shadow: inset 0 -1px 0 var(--hair);
}
.bar { display: flex; gap: 8px; align-items: center; width: 100%; min-height: 80px; }
.bar .chip {
  background: transparent; color: var(--ink); box-shadow: none;
  font: 600 14px/1 var(--sans); letter-spacing: 0; text-transform: none;
  padding: 12px 14px; border-radius: 999px;
}
.bar .chip:hover { background: var(--sunk); transform: none; }
.bar .chip:active { transform: none; }
#pairbtn {
  box-shadow: inset 0 0 0 1px var(--hair-2); padding: 11px 16px;
}
#pairbtn:hover { box-shadow: inset 0 0 0 1px var(--ink); background: var(--paper); }
.bar #controls .field input { background: var(--bar-field); color: var(--bar-ink); box-shadow: none; }
.bar #controls .field input::placeholder { color: var(--bar-dim); }
.bar #controls .field input:focus { box-shadow: 0 0 0 2px var(--gold); }
#barslot { display: flex; gap: 10px; align-items: center; flex: 1; min-width: 0; }
/* A search field stretched across a 1500px header reads as a text area, not
   a search box. */
#barslot .field { max-width: 560px; }
#browsebtn { margin-left: auto; }
#savedbtn { margin-left: 0; }
#theme { padding: 9px 12px; font-size: 15px; line-height: 1; }

/* ─── Controls (city + search) ────────────────────────────────────────────
   One instance, moved between the hero and the header rather than
   duplicated — two search boxes would mean two sources of truth for what
   the visitor typed. */
/* One pill, divided into fields. Each segment is a question, the dividers
   say they belong to the same question, and the round button at the end is
   the verb. It reads as a single control rather than three that happen to
   sit near each other. */
#controls { display: flex; align-items: center; flex: 1; min-width: 0; }
.stage #controls, .setup #controls {
  background: var(--panel); border-radius: 999px; padding: 6px 6px 6px 8px;
  box-shadow: 0 1px 2px rgba(0,0,0,.08), 0 8px 28px rgba(0,0,0,.10);
  max-width: 780px; margin: 0 auto; width: 100%;
}
.stage #controls:hover, .setup #controls:hover {
  box-shadow: 0 2px 4px rgba(0,0,0,.10), 0 14px 40px rgba(0,0,0,.14);
}
input[type=search], input[type=text], select {
  font: inherit; font-size: 15px; padding: 9px 16px; border: 0;
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
  padding: 9px 16px; background: var(--panel); color: var(--ink);
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
.citymenu button[aria-selected=true] { background: var(--pink-deep); color: var(--on-pink); }
.from, .to { font-size: 14px; color: var(--dim); font-weight: 600; white-space: nowrap; }
.to { color: var(--dim); }
.field { position: relative; display: flex; flex: 1; min-width: 0; }
.field input[type=search] { width: 100%; padding-right: 42px; }
.field .clear {
  position: absolute; right: 7px; top: 50%; transform: translateY(-50%);
  width: 28px; height: 28px; background: var(--chip); color: var(--accent-deep);
  font-size: 17px; line-height: 1; display: grid; place-items: center;
}
.field .clear[hidden] { display: none; }
/* Typing a place name exactly is a memory test nobody signed up for —
   "Torchys", "torchy's" and "Alamo" should all get you there. */
.suggest {
  position: absolute; top: calc(100% + 8px); left: 0; right: 0; z-index: 60;
  padding: 7px; border-radius: 18px; background: var(--panel);
  box-shadow: var(--lift); max-height: 320px; overflow-y: auto;
}
.suggest[hidden] { display: none; }
.suggest button {
  display: block; width: 100%; text-align: left; padding: 10px 14px;
  border-radius: 12px; background: none; color: var(--ink);
  font-size: 15px; font-weight: 600;
}
.suggest button .cat { font-size: 13px; font-weight: 400; color: var(--faint); margin-left: 10px; }
.suggest button:hover, .suggest button.on { background: var(--chip); }
.suggest button.on { background: var(--pink-deep); color: var(--on-pink); }
.suggest button.on .cat { color: var(--on-pink); opacity: .8; }
.field .clear:hover { background: var(--pink-deep); color: var(--on-pink); }

button {
  font: inherit; border: 0; border-radius: 999px; cursor: pointer;
  transition: transform .22s var(--spring), box-shadow .2s, color .2s, background .2s;
}
.chip {
  font-size: 14px; font-weight: 600; padding: 11px 16px;
  background: var(--panel); color: var(--ink); box-shadow: inset 0 0 0 1px var(--hair-2);
}
.chip:hover { color: var(--ink); box-shadow: var(--hard-sm); transform: translate(-1px, -1px); }
/* Press moves *into* the shadow, so the button behaves like a physical key. */
.chip:active { box-shadow: none; transform: translate(2px, 2px); }
/* The brand is the way home, so it should look like the biggest thing in the
   bar rather than a label sharing its baseline with the controls. */
button.brand {
  background: none; margin: 0; padding: 0 20px 0 0;
  font-size: 23px; font-weight: 800; letter-spacing: -0.035em;
  color: var(--accent); line-height: 1; align-self: center;
}
button.brand:hover { color: var(--accent-deep); }
button.brand:hover { color: var(--dim); }

/* ─── The index ───────────────────────────────────────────────────────────
   Naming a place is the whole product, so on the index it owns the screen:
   the controls sit in the middle of the viewport at full size and the
   header carries nothing but the brand and the theme toggle. */
/* Step two: the same white page and the same pill shape, asking the next
   question. Nothing about the surface changes between the two, which is the
   whole point — it should feel like one place, not two. */
.stage {
  min-height: calc(100vh - 80px);
  display: flex; flex-direction: column; justify-content: center;
  text-align: center; padding: 24px 24px 150px; gap: 4px;
  position: relative; overflow: hidden;
}
.stage::before, .stage::after {
  content: ""; position: absolute; border-radius: 50%; pointer-events: none; filter: blur(2px);
}
.stage::before { width: 34vw; height: 34vw; right: -12vw; top: -12vw; background: var(--turquoise); opacity: .06; }
.stage::after { width: 22vw; height: 22vw; left: -8vw; bottom: 6vw; background: var(--accent); opacity: .06; }
.stage .inner, .stage .tryfoot { position: relative; z-index: 1; }
.stage[hidden] { display: none; }
.stage .inner { width: 100%; max-width: min(900px, 92vw); margin: 0 auto; }
.stage #controls { justify-content: center; flex: 0 1 auto; }
.stage #controls .field { flex: 1 1 auto; max-width: none; }
.stage #controls input[type=search] {
  font-size: 17px; padding: 18px 22px; background: none; box-shadow: none;
}
.stage #controls input[type=search]:focus { box-shadow: none; }
.stage .q { font-size: 15px; font-weight: 500; color: var(--dim); margin: 40px 0 0; }

/* The search itself gets the round button, matching Next on step one. */
.field .go {
  width: 48px; height: 48px; flex: 0 0 48px; border-radius: 999px;
  background: var(--accent); color: #fff; display: grid; place-items: center;
  font-size: 18px; margin-left: 4px;
}
.field .go:hover { background: var(--accent-deep); }

.stage.typing {
  min-height: 0; overflow: visible; padding-bottom: 26px; justify-content: flex-start;
  padding-top: 28px;
}
.stage.typing::before, .stage.typing::after { display: none; }
.stage.typing .ask { font-size: clamp(20px, 2.4vw, 28px); margin-bottom: 20px; }

.tryfoot .q { font-size: 14px; font-weight: 500; color: var(--dim); margin: 0 0 14px; }
.peekcity { font-size: 14px; color: var(--faint); margin-right: 8px; }
.peekrow { font-size: 16px; color: var(--dim); }
.peekrow b { font-weight: 600; font-size: 17px; color: var(--ink); }

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
  transition: transform .75s cubic-bezier(.4, 0, .2, 1);
}
.rail.hold .track { transition: none; }   /* the seamless wrap, unanimated */
.eg {
  font-size: 14px; font-weight: 600; padding: 9px 16px; white-space: nowrap;
  background: var(--chip); color: var(--accent-deep);
}
/* Alternating between the two brand colours at low strength, rather than
   introducing two more hues purely for decoration. */
.eg:nth-child(3n+2) { background: color-mix(in srgb, var(--pink) 15%, var(--panel)); color: var(--pink-deep); }
.eg:nth-child(3n+3) { background: color-mix(in srgb, var(--accent) 15%, var(--panel)); color: var(--accent-deep); }
.eg:hover { background: var(--pink-deep); color: var(--on-pink); transform: translateY(-2px); }

.browse { padding: 56px 24px 40px; text-align: center; }
.browse[hidden] { display: none; }
.browseh {
  font-size: clamp(24px, 3vw, 34px); font-weight: 800; letter-spacing: -0.03em;
  margin: 0 0 34px; color: var(--ink);
}
.cats { max-width: 900px; margin: 0 auto; }
.cats { display: flex; gap: 8px; justify-content: center; flex-wrap: wrap; }
.cats button {
  font-size: 17px; font-weight: 650; padding: 16px 26px;
  background: var(--panel); color: var(--ink); box-shadow: var(--shadow);
}
.cats button:hover { transform: translate(-2px, -3px); box-shadow: var(--hard); }
.cats button:active { transform: translate(2px, 2px); box-shadow: none; }
.cats button .n { font-size: 14px; font-weight: 400; color: var(--faint); margin-left: 8px; }

/* ─── Results ─────────────────────────────────────────────────────────── */
main { max-width: 860px; margin: 0 auto; padding: 20px 24px 140px; }
.crumb {
  max-width: 820px; margin: 0 auto; padding: 22px 24px 4px;
  display: flex; align-items: baseline; gap: 12px;
}
.crumb[hidden] { display: none; }
.crumb span { font-size: 15px; font-weight: 600; color: var(--ink); }
/* Listings, not a newspaper index. Each answer is a card: the map reads as
   the photo, the name as the title, the roles as the amenity line. Cards say
   "these are comparable things, pick one", which is exactly the job. */
main { max-width: 1180px; margin: 0 auto; padding: 28px 40px 140px; }
.card {
  background: var(--panel); border-radius: 16px; padding: 0; margin: 0 0 8px;
  box-shadow: none; display: block;
  transition: none;
}
.card:hover { box-shadow: none; transform: none; }
.card > .head {
  display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap;
  padding: 26px 4px 14px; margin: 0; box-shadow: none;
}
.head h2 {
  font-size: 15px; font-weight: 600; letter-spacing: -0.01em;
  margin: 0; color: var(--dim); line-height: 1.2;
}
.arrow { font-size: 15px; color: var(--faint); }
.roles { display: flex; flex-wrap: wrap; gap: 6px; width: 100%; margin-top: 4px; }
.role {
  font-size: 12px; font-weight: 500; color: var(--dim);
  padding: 5px 10px; border-radius: 999px; background: var(--sunk);
}
/* The roles are the argument. Stating them in the margin turns the answer
   from an assertion into a claim with reasons attached. */
.roles { display: flex; flex-direction: column; align-items: flex-start; gap: 5px; margin-top: 14px; }
.role {
  font-size: 12.5px; font-weight: 500; color: var(--dim);
  padding: 5px 11px; border-radius: 999px; background: var(--sunk);
}
/* One block per city inside a place's card. A hairline between them, so the
   card still reads as one place rather than several. */
.city {
  padding: 22px 22px 20px; margin: 0 0 22px; border-radius: 16px;
  box-shadow: inset 0 0 0 1px var(--hair);
  transition: box-shadow .2s, transform .2s var(--spring);
}
.city:hover { box-shadow: var(--lift); transform: translateY(-2px); }
.city:first-of-type { margin-top: 0; }
.cityname {
  font-size: 13px; font-weight: 600; color: var(--accent);
  margin-bottom: 6px; display: block; letter-spacing: 0; text-transform: none;
}
.answer {
  font-size: clamp(24px, 2.6vw, 32px); font-weight: 700; letter-spacing: -0.03em;
  line-height: 1.15; margin: 0 0 12px; text-wrap: balance; color: var(--ink);
}
.answer span { background: none; }
/* ─── The map on a card ───────────────────────────────────────────────────
   Raster tiles as plain <img>s, positioned by arithmetic. No map library and
   no API key: the only thing a slippy map adds here is panning, and this is a
   thumbnail you click through to Google Maps, not something to explore.
   `loading=lazy` means a card scrolled past never fetches anything. */
.map {
  position: relative; height: 168px; width: 100%; margin: 4px 0 18px;
  border-radius: 16px; overflow: hidden; background: var(--chip);
  display: block; text-decoration: none;
}
.map img {
  position: absolute; width: 256px; height: 256px; max-width: none;
  border: 0; image-rendering: auto;
}
.map .pin {
  position: absolute; left: 50%; top: 50%; width: 16px; height: 16px;
  margin: -8px 0 0 -8px; border-radius: 999px;
  background: var(--pink); box-shadow: 0 0 0 3px #fff, 0 3px 10px rgba(7,59,76,.45);
}
/* A slow pulse, only on hover — the card is asking to be clicked, not
   flashing at you while you read. */
.map:hover .pin { animation: ping 1.4s ease-out infinite; }
@keyframes ping {
  0% { box-shadow: 0 0 0 3px #fff, 0 0 0 0 color-mix(in srgb, var(--pink) 60%, transparent); }
  100% { box-shadow: 0 0 0 3px #fff, 0 0 0 18px transparent; }
}
.map .osm {
  position: absolute; right: 0; bottom: 0; font-size: 9.5px; line-height: 1.6;
  padding: 1px 6px; background: color-mix(in srgb, var(--panel) 82%, transparent);
  color: var(--faint); border-radius: 6px 0 0 0;
}
.map:hover { box-shadow: var(--lift); }

/* Actions under an answer: where to go, and whether you're keeping it. */
.acts { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-top: 16px; }
.acts a, .acts .save {
  font-size: 14px; font-weight: 600; padding: 11px 16px;
  border-radius: 999px; background: var(--chip); color: var(--accent-deep);
  text-decoration: none; display: inline-flex; align-items: center; gap: 6px;
  transition: background .2s, color .2s, transform .22s var(--spring);
}
.acts a:hover, .acts .save:hover {
  background: var(--pink-deep); color: var(--on-pink); transform: translateY(-2px);
}
/* A save is yours, not the site's — pink marks it as the one thing on the
   page you put there. */
.acts .save[aria-pressed=true] { background: var(--pink-deep); color: var(--on-pink); }
.acts .save[aria-pressed=true]:hover { background: var(--pink-deep); }
.acts .out { font-size: 11px; opacity: .6; }
.savecount { font-size: 12px; color: var(--faint); font-weight: 600; margin-left: 4px; }
#savedbtn[hidden] { display: none; }

.why { color: var(--dim); font-size: 15px; line-height: 1.7; margin-top: 6px; max-width: 60ch; }
.why.big { font-size: 15px; line-height: 1.6; color: var(--dim); opacity: 1; max-width: 68ch; }
.cname { font-weight: 600; font-size: 17px; letter-spacing: -0.01em; }
.alt { padding: 14px 0 0 18px; box-shadow: inset 2px 0 0 var(--hair); margin-top: 14px; }
details { margin-top: 18px; }
summary {
  cursor: pointer; font-size: 14px; font-weight: 600; color: var(--ink);
  text-decoration: underline; text-underline-offset: 3px;
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
summary:hover { color: var(--dim); }
.link {
  font: inherit; font-size: 13px; background: none; color: var(--faint);
  cursor: pointer; text-decoration: underline; text-underline-offset: 3px;
  padding: 0; transition: color .2s;
}
.link:hover { color: var(--dim); }
.empty { text-align: center; color: var(--faint); padding: 90px 20px; font-size: 17px; }
.more { text-align: center; color: var(--dim); font-size: 14px; padding: 16px 20px 48px; }

/* ─── Step one: the two cities ─────────────────────────────────────────
   White, like everything else. The discs stay — they were the good part of
   the yellow — but at low opacity and behind everything, so they read as
   atmosphere rather than as a background you have to work against. */
.setup {
  min-height: 100vh; display: flex; flex-direction: column; justify-content: center;
  padding: 40px 5vw 80px; position: relative; overflow: hidden; text-align: center;
}
.setup[hidden] { display: none; }
.setup .inner { width: 100%; max-width: 860px; margin: 0 auto; }
.setup::before, .setup::after {
  content: ""; position: absolute; border-radius: 50%; pointer-events: none;
  filter: blur(2px);
}
.setup::before { width: 38vw; height: 38vw; right: -12vw; top: -14vw; background: var(--accent); opacity: .07; }
.setup::after { width: 26vw; height: 26vw; left: -10vw; bottom: -10vw; background: var(--turquoise); opacity: .07; }
.setup .inner > * { position: relative; z-index: 1; }

.setup .pitch {
  font-size: clamp(34px, 4.6vw, 56px); font-weight: 800; letter-spacing: -0.035em;
  line-height: 1.08; margin: 0 0 20px; color: var(--ink);
}
/* Second line of the slogan — it needs its own line, or the comma runs
   straight into "It's". */
.setup .pitch em { font-style: normal; color: var(--accent); display: block; }
.setup #heroplace { color: var(--accent); }
.setup .sub {
  font-size: 19px; color: var(--dim); max-width: 46ch; margin: 0 auto 44px; line-height: 1.55;
}
.setup .sub em { color: var(--ink); font-style: normal; font-weight: 600; }

/* The pair, as one control: two segments, a divider, and the verb at the
   end. Same shape as the search pill on the next screen, so moving from one
   to the other feels like the same object asking a second question. */
.pair {
  counter-reset: leg; display: flex; align-items: center; gap: 0;
  background: var(--panel); border-radius: 999px; padding: 6px 6px 6px 10px;
  box-shadow: 0 1px 2px rgba(0,0,0,.08), 0 8px 28px rgba(0,0,0,.10);
  max-width: 720px; margin: 0 auto 24px; text-align: left;
}
.pair:hover { box-shadow: 0 2px 4px rgba(0,0,0,.10), 0 14px 40px rgba(0,0,0,.14); }
.leg {
  display: flex; flex-direction: column; gap: 2px; align-items: flex-start;
  padding: 12px 22px; flex: 1; min-width: 0; border-radius: 999px;
}
.leg:hover { background: var(--sunk); }
.leg + .leg { box-shadow: inset 1px 0 0 var(--hair); }
.legt {
  font: 600 12px/1 var(--sans); letter-spacing: 0; text-transform: none;
  color: var(--ink); display: flex; align-items: center; gap: 7px;
}
.legt::before {
  content: counter(leg, decimal-leading-zero); counter-increment: leg;
  font: 600 10px/1 var(--mono); color: var(--accent);
}
.leg .citybtn {
  font-size: 15px; font-weight: 400; letter-spacing: 0; padding: 2px 0;
  background: none; box-shadow: none; color: var(--dim);
}
.leg .citybtn:hover { box-shadow: none; color: var(--ink); transform: none; background: none; }
.leg .citybtn .caret { color: var(--faint); }

/* Round, coral, and at the end of the pill — the one thing on the screen
   that does something. */
.next {
  font-size: 16px; font-weight: 600; letter-spacing: 0; text-transform: none;
  padding: 16px 26px; background: var(--accent); color: var(--on-accent);
  box-shadow: none; border-radius: 999px; white-space: nowrap;
}
.next:hover { background: var(--accent-deep); transform: none; box-shadow: none; }
.next:active { transform: scale(.97); box-shadow: none; }

/* Step two asks one thing. */
.ask {
  font-weight: 800; font-size: clamp(30px, 4.2vw, 52px); letter-spacing: -0.035em;
  line-height: 1.06; margin: 0 0 36px; text-wrap: balance; color: var(--ink);
}
/* Flat, not a gradient. A colour ramp across four letters reads as an
   effect applied to the word rather than as the word being the point. */
.ask em { font-style: normal; color: var(--accent); }
/* The pair you chose, always visible and always one click from changing. */
#pairbtn { font-weight: 650; }

/* ─── First visit: choose a city ──────────────────────────────────────── */
.pick { padding: 76px 24px 40px; text-align: center; }
.pick[hidden] { display: none; }
.pick .wrap { max-width: 700px; margin: 0 auto; }
.pick .pick .brand { font-family: var(--display); font-size: 17px; color: var(--dim); margin-bottom: 26px; }
.sub { font-size: 17px; color: var(--dim); max-width: 30em; margin: 0 auto 44px; line-height: 1.55; }
.pick .q { font-size: 15px; font-weight: 650; color: var(--dim); margin: 0 0 16px; }
.cities { display: flex; gap: 12px; justify-content: center; flex-wrap: wrap; }
.cities button {
  font-size: 19px; font-weight: 700; letter-spacing: -0.02em;
  padding: 20px 34px; border-radius: 20px;
  background: var(--panel); color: var(--ink); box-shadow: var(--shadow);
}
.cities button:hover { transform: translateY(-3px); box-shadow: var(--lift); color: var(--dim); }
.cities button:active { transform: translateY(0) scale(.97); }

#app[hidden] { display: none; }

/* Narrow windows: the bar has a brand, two city pickers, a search box and
   three buttons, which is more than fits. Let it wrap and give the search
   its own row rather than letting Saved slide under Chicago. */
@media (max-width: 860px) {
  .bar { flex-wrap: wrap; row-gap: 8px; }
  #barslot { order: 3; flex: 1 0 100%; }
  #barslot .field { max-width: none; }
}
@media (max-width: 640px) {
  header { padding: 8px 14px; }
  button.brand { font-size: 19px; padding-right: 8px; }
  .bar { gap: 10px; }
  .from { display: none; }        /* "I know" is implied by the city control */
  main { padding: 8px 16px 70px; }
  .crumb { padding: 16px 18px 2px; }
  .card { grid-template-columns: 1fr; gap: 18px; padding: 24px 0 34px; }
  .card > .head { position: static; }
  .answer { font-size: 27px; }
  .pitch { margin-bottom: 24px; }
  .sub { font-size: 16px; margin-bottom: 32px; }
  .cities button { font-size: 17px; padding: 16px 24px; flex: 1 1 40%; }
  .stage { padding: 16px 18px 140px; min-height: calc(100vh - 54px); }
  .setup { padding: 28px 18px 40px; }
  .pair { gap: 14px; margin: 28px 0 26px; }
  .leg { width: 100%; }
  .leg .citypick, .leg .citybtn { width: 100%; }
  .tryfoot { padding: 12px 14px 16px; }
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
<!-- Step one: where you're coming from and where you're going. On one
     screen with the search box, this was three simultaneous decisions and
     no indication which to make first. Split out, each screen asks one
     question. The city pickers live here and nowhere else. -->
<section id="setup" class="setup" hidden>
  <div class="inner">
    <h1 class="pitch"><span class="l1" id="line1">Every city has <span id="heroart">an</span> <span id="heroplace">H-E-B</span>,</span><em>It\'s just elsewhere.</em></h1>
    <p class="sub">Tell us the city you know and the one you're headed to.
    We\'ll find the counterparts — not the same category, the same <em>role</em>.</p>

    <div class="pair">
      <label class="leg">
        <span class="legt">I know</span>
        <div class="citypick">
          <select id="srcsel" class="native" title="Which city you know" tabindex="-1"></select>
          <button class="citybtn" id="citybtn" aria-haspopup="listbox" aria-expanded="false"></button>
          <div class="citymenu" id="citymenu" role="listbox" hidden></div>
        </div>
      </label>
      <label class="leg">
        <span class="legt">I\'m traveling to</span>
        <div class="citypick">
          <select id="dstsel" class="native" title="Which city you\'re going to" tabindex="-1"></select>
          <button class="citybtn" id="dstbtn" aria-haspopup="listbox" aria-expanded="false"></button>
          <div class="citymenu" id="dstmenu" role="listbox" hidden></div>
        </div>
      </label>
      <button class="next" id="nextbtn">Next</button>
    </div>
  </div>
</section>

<div id="app" hidden>
  <header id="bar"><div class="bar">
    <button class="brand" id="homebtn" title="Start over">Elsewhere</button>
    <!-- #controls lives here on results pages and in the hero on step two;
         the same element either way. -->
    <div id="barslot"></div>
    <button class="chip" id="pairbtn" title="Change cities"></button>
    <button class="chip" id="browsebtn" title="Browse by category">Browse</button>
    <button class="chip" id="savedbtn" hidden title="Places you saved"></button>
    <button class="chip" id="theme" title="Switch theme" aria-label="Switch theme">☼</button>
  </div></header>

  <div id="controls">
    <div class="field">
      <input type="search" id="q" placeholder="Name a place you love…"
             autocomplete="off" role="combobox" aria-autocomplete="list"
             aria-expanded="false" aria-controls="suggest">
      <button class="clear" id="clearq" hidden aria-label="Clear">×</button>
      <div class="suggest" id="suggest" role="listbox" hidden></div>
    </div>
  </div>

  <!-- Step two: one question, one box. -->
  <section class="stage" id="prompt">
    <div class="inner">
      <h1 class="ask" id="askh"></h1>
      <div id="heroslot"></div>
      <div class="peek" id="peek"></div>
    </div>
    <!-- Pinned to the bottom of the viewport rather than trailing the hero:
         it's an ambient sample of what's in here, not the next step in the
         sentence. -->
    <div class="tryfoot" id="tryfoot">
      <p class="q" id="promptq"></p>
      <div class="rail"><div class="track" id="examples"></div></div>
    </div>
  </section>

  <section class="browse" id="browsepage" hidden>
    <h2 class="browseh" id="browseh"></h2>
    <div class="cats" id="cats"></div>
  </section>

  <div class="crumb" id="crumb" hidden>
    <span id="crumbtext"></span>
    <button class="link" id="crumbclear">clear</button>
  </div>

  <main id="list"></main>
</div>

<script>
let S = null, q = "", cat = "", dest = "";
//: True once the visitor has settled on a query — Enter, or a suggestion
//: picked — as opposed to still typing one. Only a commit is allowed to
//: rearrange the page.
let committed = false;
//: How many cards to build at once. Past this it's a scroll nobody finishes,
//: and each card's map is six more elements to lay out.
const LIST_CAP = 25;
let home = localStorage.getItem("elsewhere.home") || "";
let savedDest = localStorage.getItem("elsewhere.dest") || "";

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
document.getElementById("theme").addEventListener("click", () => {
  applyTheme(document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark");
  if (typeof S !== "undefined" && S) render();   // maps are light or dark tiles
});
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
  const answers = dest && m.cities[dest] ? [m.cities[dest]] : Object.values(m.cities);
  const body = norm(m.roles.join(" ") + " " +
    answers.flatMap(c => c.candidates.map(x => x.name + " " + x.reasoning)).join(" "));
  return body.includes(needle) ? 1 : 0;
}

/* ── Suggestions ───────────────────────────────────────────────────────
   `score` decides whether a place answers a finished query. This decides
   what to offer while someone is still typing, which is a different job: it
   has to tolerate a half-typed word, a missing apostrophe, and a name the
   visitor only half-remembers.

   Subsequence matching does the fuzzy part — every typed character has to
   appear in order, so "trchy" still reaches "Torchy's Tacos" — and the gaps
   are penalised so tightly-packed matches rank above scattered ones. */
function fuzzy(needle, hay) {
  if (!needle) return 0;
  if (hay.startsWith(needle)) return 1000 - hay.length;
  const at = hay.indexOf(needle);
  if (at >= 0) return 700 - at * 2 - hay.length;

  let i = 0, gaps = 0, last = -1, first = -1;
  for (const ch of needle) {
    const found = hay.indexOf(ch, i);
    if (found < 0) return 0;
    if (first < 0) first = found;
    if (last >= 0) gaps += found - last - 1;
    last = found;
    i = found + 1;
  }
  // Two guards, both learned the hard way. A subsequence has to begin at the
  // start of a word — otherwise "heb" matches the alias "the bats", the same
  // collision that once made "heb" return 72 results. And it has to be
  // tightly packed, or a long enough name contains almost any short query.
  if (first > 0 && hay[first - 1] !== " ") return 0;
  if (gaps > 8) return 0;
  return Math.max(1, 400 - gaps * 8 - hay.length);
}

/* One typo's worth of slack. Dropping each character in turn covers the
   common cases — a doubled letter, a stray key — so "franklyn" still reaches
   Franklin. Anything looser starts matching everything. */
function fuzzyish(needle, hay) {
  let best = fuzzy(needle, hay);
  // Length is counted without spaces, and spaces are never the character
  // dropped: deleting the space in "h e b" turns a precise query into a
  // three-letter soup that matches half the corpus.
  if (best || needle.replace(/ /g, "").length < 5) return best;
  for (let i = 0; i < needle.length; i++) {
    if (needle[i] === " ") continue;
    const sc = fuzzy(needle.slice(0, i) + needle.slice(i + 1), hay);
    if (sc > best) best = sc - 40;   // a corrected match ranks below a clean one
  }
  return Math.max(0, best);
}

/* The space-stripped comparison exists for one reason: "heb" has to reach
   "H-E-B", which normalizes to "h e b". Stripping spaces also erases the word
   boundaries the subsequence guard depends on, so it's allowed to match a
   prefix or a substring and nothing looser. */
function tight(needle, hay) {
  // Prefix only. Allowing a substring here brings back the same collision in
  // yet another disguise: with spaces gone, "heb" sits inside "theblanton"
  // and "thebats". Real uses of this path are all prefixes — "heb" for
  // "h e b", "hebgrocery" for "h e b grocery".
  return needle && hay.startsWith(needle) ? 1000 - hay.length : 0;
}

function suggestions(text, limit = 7) {
  const needle = norm(text);
  // A single letter matches most of the corpus, so the list it produces is
  // noise that covers the page rather than help.
  if (needle.length < 2 || !S) return [];
  return S.matches
    .filter(m => m.cities[dest])
    .map(m => {
      const best = Math.max(...[m.name, ...(m.aliases || [])].map(n => {
        const k = norm(n);
        return Math.max(fuzzyish(needle, k),
                        tight(needle.replace(/ /g, ""), k.replace(/ /g, "")));
      }));
      return [best, m];
    })
    .filter(([sc]) => sc > 0)
    .sort((a, b) => b[0] - a[0] || a[1].name.localeCompare(b[1].name))
    .slice(0, limit)
    .map(([, m]) => m);
}

let sugAt = -1;

function renderSuggest() {
  const el = document.getElementById("suggest");
  const box = document.getElementById("q");
  const rows = q ? suggestions(q) : [];
  // One exact hit that's already showing isn't a suggestion, it's an echo.
  const only = rows.length === 1 && norm(rows[0].name) === norm(q);
  if (!rows.length || only) {
    el.innerHTML = "";
    el.hidden = true;
    box.setAttribute("aria-expanded", "false");
    return;
  }
  sugAt = -1;
  el.innerHTML = rows.map((m, i) =>
    `<button role="option" data-name="${esc(m.name)}" data-i="${i}">${esc(m.name)}
       <span class="cat">${esc(groupOf(m))}</span></button>`).join("");
  el.hidden = false;
  box.setAttribute("aria-expanded", "true");
}

function closeSuggest() {
  document.getElementById("suggest").hidden = true;
  document.getElementById("q").setAttribute("aria-expanded", "false");
  sugAt = -1;
}

function moveSuggest(step) {
  const items = [...document.querySelectorAll("#suggest button")];
  if (!items.length) return;
  items.forEach(b => b.classList.remove("on"));
  sugAt = (sugAt + step + items.length + 1) % (items.length + 1) - 1;
  if (sugAt >= 0) {
    items[sugAt].classList.add("on");
    items[sugAt].scrollIntoView({ block: "nearest" });
  }
}

function pickSuggest(name) {
  q = name; cat = ""; committed = true;
  document.getElementById("q").value = name;
  closeSuggest();
  syncURL(); render();
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
  for (const m of S.matches) {
    if (!m.cities[dest]) continue;
    counts[groupOf(m)] = (counts[groupOf(m)] || 0) + 1;
  }
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
  if (dest) p.set("to", dest);
  if (q) p.set("q", q);
  else if (cat === "__saved") p.set("saved", "1");
  else if (cat === "__browse") p.set("browse", "1");
  else if (cat) p.set("in", cat);
  const url = p.toString() ? "?" + p : location.pathname;
  history[replace ? "replaceState" : "pushState"]({ home, q, cat }, "", url);
}

function readURL() {
  const p = new URLSearchParams(location.search);
  return {
    city: p.get("city") || "",
    dest: p.get("to") || "",
    q: p.get("q") || "",
    cat: p.get("saved") ? "__saved" : p.get("browse") ? "__browse" : (p.get("in") || ""),
  };
}

addEventListener("popstate", () => {
  const u = readURL();
  q = u.q; cat = u.cat;
  document.getElementById("q").value = q;
  if (u.city && u.city !== home) { home = u.city; dest = u.dest; load(); }
  else if (u.dest && u.dest !== dest) { dest = u.dest; drawDst(); render(); }
  else { render(); }
});

/* Web Mercator, the same projection every tile server uses: longitude is
   linear, latitude runs through a log-tangent so the tiles stay square.
   Returns the place's position in world pixels at this zoom. */
/* CARTO's Positron, not openstreetmap.org's own tiles: those are served by
   volunteer infrastructure whose usage policy this page would be breaking,
   and they say so — the request comes back 418 with a "tile usage policy"
   image in place of the map. Positron needs no key, comes in a light and a
   dark cut that match the two themes, and its washed-out styling is the
   right register for a thumbnail behind a pin. Attribution is required and
   sits in the corner. */
const MAP_ZOOM = 15, TILE = 256, MAP_H = 132;
const mapStyle = () =>
  document.documentElement.getAttribute("data-theme") === "dark" ? "dark_all" : "light_all";

function worldPixels(lat, lon, z) {
  const n = TILE * Math.pow(2, z);
  const s = Math.sin(lat * Math.PI / 180);
  return {
    x: (lon + 180) / 360 * n,
    y: (0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI)) * n,
    n,
  };
}

/* A mosaic of whole tiles, shifted so the place lands dead centre. Sized
   generously in width because a card is wider than it is tall and the extra
   tiles are cheap; anything off the edge is clipped by overflow. */
function mapHTML(l, width = 560) {
  if (l.lat === undefined || l.lon === undefined) return "";
  const { x, y } = worldPixels(l.lat, l.lon, MAP_ZOOM);
  const left = x - width / 2, top = y - MAP_H / 2;
  const tiles = [];
  for (let tx = Math.floor(left / TILE); tx <= Math.floor((left + width) / TILE); tx++) {
    for (let ty = Math.floor(top / TILE); ty <= Math.floor((top + MAP_H) / TILE); ty++) {
      tiles.push(`<img loading="lazy" alt="" src="https://basemaps.cartocdn.com/${
        mapStyle()}/${MAP_ZOOM}/${tx}/${ty}.png"
        style="left:${tx * TILE - left}px;top:${ty * TILE - top}px">`);
    }
  }
  return `<a class="map" href="${esc(l.map)}" target="_blank" rel="noopener noreferrer"
     aria-label="Open in Google Maps">${tiles.join("")}<span class="pin"></span>
     <span class="osm">&copy; OpenStreetMap &copy; CARTO</span></a>`;
}

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
  return `${mapHTML(l)}<div class="acts">${site}${map}
    <button class="save" data-city="${esc(city)}" data-name="${esc(place.name)}"
      data-from="${esc(from || "")}" aria-pressed="false"><span>\u2606 Save</span></button>
  </div>`;
}

/* ── Rendering ─────────────────────────────────────────────────────── */
function render() {
  if (!S) return;

  const browsing = cat === "__browse";
  const idle = !q && !cat;
  if (!idle) stopDemo();
  document.getElementById("browsepage").hidden = !browsing;
  if (browsing) {
    document.getElementById("browseh").textContent =
      `What are you looking for in ${title(dest)}?`;
    renderCats();
  }
  /* Where the search box lives.

     It used to jump into the header on the first character typed, which
     reorganised the page mid-word: the box you were typing into teleported
     to the top and results filled the space it left. Re-parenting also drops
     focus silently, so the caret went with it.

     Now it only moves on a *deliberate* commit — picking a suggestion,
     pressing Enter, or opening browse. While you're still typing it stays
     exactly where you started, and the results grow underneath it. */
  const inHero = !committed && !browsing && cat !== "__saved";
  const slot = document.getElementById(inHero ? "heroslot" : "barslot");
  const controls = document.getElementById("controls");
  if (controls.parentElement !== slot) {
    const box = document.getElementById("q");
    const had = document.activeElement === box;
    const at = had ? box.selectionStart : null;
    const to = had ? box.selectionEnd : null;
    slot.appendChild(controls);
    if (had) {
      box.focus();
      try { box.setSelectionRange(at, to); } catch { /* type=search on old Safari */ }
    }
  }

  // Typing shrinks the hero rather than replacing it: the heading stays, the
  // stage stops filling the viewport, and results appear below.
  const stage = document.getElementById("prompt");
  stage.classList.toggle("typing", !idle && inHero);
  document.getElementById("tryfoot").hidden = !idle;

  stage.hidden = !idle && !inHero;
  document.getElementById("crumb").hidden = idle || browsing || !!q;
  document.getElementById("clearq").hidden = !q;
  if (idle || browsing) { document.getElementById("list").innerHTML = ""; return; }

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
    rows = S.matches.filter(m => groupOf(m) === cat && m.cities[dest]);
    const label = (GROUPS.find(([k]) => k === cat) || [cat, cat])[1];
    document.getElementById("crumbtext").textContent =
      `${label} in ${title(S.source)} \u2192 ${title(dest)} · ${rows.length} places`;
  }

  const el = document.getElementById("list");
  if (!rows.length) {
    el.innerHTML = `<p class="empty">Nothing here called “${esc(q)}”. Try another place.</p>`;
    return;
  }

  // A broad query matches most of the corpus, and every card carries a map
  // built from six tile elements. Rendering all of them cost ~140ms per
  // keystroke — felt as the input stuttering — to produce a list nobody
  // scrolls. Show the best of them and say what was left.
  const total = rows.length;
  rows = rows.slice(0, LIST_CAP);

  el.innerHTML = rows.map(m => {
    // One place, its answer in every city we can answer for.
    const blocks = S.targets.filter(t => t === dest && m.cities[t]).map(t => {
      const c = m.cities[t];
      const top = c.candidates[0];
      const rest = c.candidates.slice(1);
      return `<div class="city">
        <div class="cityname">in ${esc(title(t))}</div>
        <div class="answer"><span>${esc(top.name)}</span></div>
        <div class="why big">${esc(top.reasoning)}</div>
        ${acts(top, t, m.name)}
        ${rest.length ? `<details><summary>${rest.length} other option${
          rest.length > 1 ? "s" : ""}</summary>${rest.map(x =>
          `<div class="alt"><span class="cname">${esc(x.name)}</span>
             <div class="why">${esc(x.reasoning)}</div></div>`).join("")}</details>` : ""}
      </div>`;
    }).join("");

    const roles = (m.roles || []).slice(0, 3).map(r =>
      `<span class="role">${esc(r.replace(/_/g, " "))}</span>`).join("");
    return `<div class="card">
      <div class="head"><h2>${esc(m.name)}</h2>
        <span class="arrow">${esc(title(S.source))}</span>
        ${roles ? `<div class="roles">${roles}</div>` : ""}</div>
      ${blocks}
    </div>`;
  }).join("") + (total > rows.length
    ? `<p class="more">${total - rows.length} more match \u201c${esc(q || cat)}\u201d — keep typing to narrow it down.</p>`
    : "");
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
          <div class="answer"><span>${esc(r.name)}</span></div>
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

/* Uneven and unhurried. A fixed interval turns the rail into a metronome you
   start anticipating instead of reading; a random extra beat keeps it in the
   background where it belongs. */
function startRail() {
  clearTimeout(railTimer);
  if (matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  const next = () => {
    railTimer = setTimeout(() => { stepRail(); next(); }, 4200 + Math.random() * 2600);
  };
  next();
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

/* Shrink the claim until it fits on one line. Measured rather than guessed:
   "Every city has a Congress Avenue Bridge Bats" and "has an H-E-B" are wildly
   different lengths, and any fixed size is either too small for one or too
   wide for the other. */
function fitLine() {
  const line = document.getElementById("line1");
  if (!line) return;
  const box = line.parentElement;
  line.style.setProperty("--fit", 1);
  const room = box.clientWidth;
  if (!room || !line.scrollWidth) return;
  line.style.setProperty("--fit", Math.min(1, room / line.scrollWidth).toFixed(3));
}
addEventListener("resize", fitLine);

/* Letter-by-letter resolve, in scattered order.

   The characters don't settle left to right — each position gets a random
   place in the queue, so the new name surfaces in pieces from all over the
   word. Left-to-right reads as a cursor typing; scattered reads as a word
   developing, which is what "find me the equivalent" should look like.

   The active band starts one character wide and widens as it goes, so the
   effect begins as a flicker rather than exploding on frame one. Ahead of the
   band the old name is still legible and behind it the new one has landed:
   both are on screen at once, which is the whole point — you watch the Austin
   place turn into the Chicago one instead of watching noise. */
const SCRAMBLE = "ABCDEFGHIJKLMNOPQRSTUVWXYZ&'-";
const randChar = () => SCRAMBLE[(Math.random() * SCRAMBLE.length) | 0];

function scrambleTo(el, text) {
  clearInterval(scrambleTimer);
  const from = el.dataset.text || el.textContent || "";
  el.dataset.text = text;
  if (matchMedia("(prefers-reduced-motion: reduce)").matches) {
    el.textContent = text;
    fitLine();
    return;
  }

  const span = Math.max(from.length, text.length);
  // rank[i] is when position i resolves. Fisher-Yates over the positions,
  // then inverted, so every character has exactly one turn and no position
  // is favoured.
  const order = [...Array(span).keys()];
  for (let i = span - 1; i > 0; i--) {
    const j = (Math.random() * (i + 1)) | 0;
    [order[i], order[j]] = [order[j], order[i]];
  }
  const rank = new Array(span);
  order.forEach((pos, r) => (rank[pos] = r));

  let head = 0;
  scrambleTimer = setInterval(() => {
    head += 0.9;
    const band = 1 + (head / span) * 5;
    if (head - band > span) {
      el.textContent = text;
      fitLine();
      clearInterval(scrambleTimer);
      return;
    }
    let out = "";
    for (let i = 0; i < span; i++) {
      const r = rank[i];
      if (r < head - band) out += text[i] ?? "";
      else if (r < head) out += (text[i] === " " || from[i] === " ") ? " " : randChar();
      else out += from[i] ?? "";
    }
    el.textContent = out;
    fitLine();
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

/* `mine` is the safety catch. The demo owns the box only while it's
   unfocused and empty of anything a person put there. Without this, a missed
   stop signal means the animation types over someone mid-word — which is
   exactly what it did. */
const mine = box => demoOn && document.activeElement !== box;

async function typeInto(box, text, token) {
  for (let i = 1; i <= text.length; i++) {
    if (token !== demoRun || !mine(box)) return false;
    box.value = text.slice(0, i);
    await sleep(beat(52));
  }
  return token === demoRun;
}

async function deleteFrom(box, token) {
  while (box.value.length) {
    if (token !== demoRun || !mine(box)) return false;
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

    peek.innerHTML = S.targets.filter(t => t === dest && m.cities[t]).map(t =>
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
  fitLine();
  document.getElementById("peek").innerHTML = "";
  document.getElementById("peek").classList.remove("in");
  const box = document.getElementById("q");
  // Only clear what the demo itself left behind.
  if (!q && document.activeElement !== box) box.value = "";
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
  // The heading already asks the question; the rail just labels its samples.
  document.getElementById("promptq").textContent = `Popular in ${title(S.source)}`;
}

async function load() {
  S = await (await fetch("/api/state?source=" + encodeURIComponent(home))).json();
  home = S.source;
  localStorage.setItem("elsewhere.home", home);
  document.getElementById("srcsel").innerHTML = Object.keys(S.sources).map(c =>
    `<option value="${c}" ${c === S.source ? "selected" : ""}>${title(c)}</option>`).join("");

  // Keep the chosen destination if this city can answer for it; otherwise
  // fall back to the remembered one, then to the first available.
  if (!S.targets.includes(dest)) dest = "";
  if (!dest && S.targets.includes(savedDest)) dest = savedDest;
  if (!dest) dest = S.targets[0];
  savedDest = dest;
  localStorage.setItem("elsewhere.dest", dest);
  document.getElementById("dstsel").innerHTML = S.targets.map(c =>
    `<option value="${c}" ${c === dest ? "selected" : ""}>${title(c)}</option>`).join("");
  renderExamples();
  drawSrc(); drawDst();
  document.getElementById("pairbtn").textContent =
    `${title(S.source)} \u2192 ${title(dest)}`;
  document.getElementById("askh").innerHTML =
    `What do you love in <em>${esc(title(S.source))}</em>?`;
  render();
  renderSavedBtn();
  railAt = 0;
  document.getElementById("examples").style.transform = "translateX(0)";
  startRail();
  if (!q && !cat) startDemo();
}

/* Two screens, one question each. `showSetup` is the only thing that decides
   which is on. */
function showSetup(on) {
  document.getElementById("setup").hidden = !on;
  document.getElementById("app").hidden = on;
  if (on) stopDemo();
}

async function boot() {
  // A shared link carries its whole state and skips the picker — someone who
  // was sent a result should land on it, not on a form.
  const u = readURL();
  if (u.city) home = u.city;
  if (u.dest) dest = u.dest;
  q = u.q; cat = u.cat;

  const cities = await (await fetch("/api/cities")).json();
  if (!home || !cities.includes(home)) home = cities[0];

  // Populate the pickers before anything is shown, so Next is one click for
  // anyone who's been here before.
  document.getElementById("srcsel").innerHTML = cities.map(c =>
    `<option value="${c}" ${c === home ? "selected" : ""}>${title(c)}</option>`).join("");
  drawSrc();
  await fillDestinations();

  committed = !!(u.q || u.cat);
  if (u.city && u.dest) {
    showSetup(false);
    load().then(() => { document.getElementById("q").value = q; render(); });
  } else {
    showSetup(true);
  }
}

/* The destinations a city can actually answer for. Asked of the API rather
   than assumed, because coverage is uneven — every pair is generated
   separately and some don't exist yet. */
async function fillDestinations() {
  const st = await (await fetch("/api/state?source=" + encodeURIComponent(home))).json();
  S = st;
  home = st.source;
  if (!st.targets.includes(dest)) dest = "";
  if (!dest && st.targets.includes(savedDest)) dest = savedDest;
  if (!dest) dest = st.targets[0];
  document.getElementById("dstsel").innerHTML = st.targets.map(c =>
    `<option value="${c}" ${c === dest ? "selected" : ""}>${title(c)}</option>`).join("");
  drawDst();
}

function startAsking() {
  localStorage.setItem("elsewhere.home", home);
  localStorage.setItem("elsewhere.dest", dest);
  savedDest = dest;
  q = ""; cat = ""; committed = false;
  document.getElementById("q").value = "";
  showSetup(false);
  syncURL();
  load().then(() => document.getElementById("q").focus());
}

document.getElementById("nextbtn").addEventListener("click", startAsking);
document.getElementById("pairbtn").addEventListener("click", () => showSetup(true));

/* Delegated rather than inline: an onclick built by interpolation breaks on
   any name containing an apostrophe — Lou Malnati's, Torchy's, Mariano's —
   and does so silently, because the attribute is a JS syntax error. */
document.getElementById("examples").addEventListener("click", e => {
  const b = e.target.closest(".eg");
  if (!b) return;
  q = b.dataset.name; cat = ""; committed = true;
  stopDemo();
  document.getElementById("q").value = q;
  syncURL(); render();
});
/* Listened for on the document, in the capture phase, so nothing can swallow
   them first. Scoping these to the search box meant a click the box didn't
   receive left the animation running under the visitor's hands. */
["pointerdown", "keydown", "wheel", "touchstart"].forEach(evt =>
  document.addEventListener(evt, stopDemo, { capture: true, passive: true }));
document.getElementById("q").addEventListener("focus", stopDemo);

/* Suggestions are cheap and must feel instant. Building the result cards is
   not, so it waits for a pause in typing — otherwise every character pays for
   a list that the next character throws away. */
let renderTimer = null;

document.getElementById("q").addEventListener("input", e => {
  q = e.target.value.trim();
  if (q) cat = "";
  committed = false;          // still choosing; don't rearrange the page
  syncURL(true);   // replace, so typing doesn't fill the back stack
  renderSuggest();
  clearTimeout(renderTimer);
  renderTimer = setTimeout(render, 130);
});

document.getElementById("q").addEventListener("keydown", e => {
  const open = !document.getElementById("suggest").hidden;
  if (e.key === "ArrowDown" && open) { e.preventDefault(); moveSuggest(1); }
  else if (e.key === "ArrowUp" && open) { e.preventDefault(); moveSuggest(-1); }
  else if (e.key === "Escape") closeSuggest();
  else if (e.key === "Enter") {
    const on = document.querySelector("#suggest button.on");
    if (on) { e.preventDefault(); pickSuggest(on.dataset.name); }
    else { closeSuggest(); committed = true; render(); }
  }
});

document.getElementById("suggest").addEventListener("click", e => {
  const b = e.target.closest("button[data-name]");
  if (b) pickSuggest(b.dataset.name);
});
// A click anywhere else means they're done with the list.
addEventListener("click", e => {
  if (!e.target.closest(".field")) closeSuggest();
});
/* ── City menus ────────────────────────────────────────────────────────
   Two of them — where you know and where you're going — sharing one
   implementation. Each is a skin over a real <select>, which stays in the DOM
   as the single source of truth and for keyboard and screen reader support. */
function cityMenu(selId, btnId, menuId, onPick) {
  const sel = document.getElementById(selId);
  const btn = document.getElementById(btnId);
  const menu = document.getElementById(menuId);

  function draw() {
    btn.innerHTML = `${esc(title(sel.value))}<span class="caret">\u25BC</span>`;
    menu.innerHTML = [...sel.options].map(o =>
      `<button role="option" data-city="${esc(o.value)}"
         aria-selected="${o.value === sel.value}">${esc(o.textContent)}</button>`).join("");
  }

  btn.addEventListener("click", e => {
    e.stopPropagation();
    const open = menu.hidden;
    closeMenus();
    menu.hidden = !open;
    btn.setAttribute("aria-expanded", open);
  });
  menu.addEventListener("click", e => {
    const b = e.target.closest("button[data-city]");
    if (!b) return;
    closeMenus();
    if (b.dataset.city === sel.value) return;
    sel.value = b.dataset.city;
    draw();
    onPick(b.dataset.city);
  });
  return draw;
}

function closeMenus() {
  document.querySelectorAll(".citymenu").forEach(m => (m.hidden = true));
  document.querySelectorAll(".citybtn").forEach(b => b.setAttribute("aria-expanded", "false"));
}
// Click-away and Escape, the two ways every other menu on the web closes.
addEventListener("click", closeMenus);
addEventListener("keydown", e => { if (e.key === "Escape") closeMenus(); });

const drawSrc = cityMenu("srcsel", "citybtn", "citymenu", async city => {
  home = city;
  dest = "";                      // the old destination may not exist from here
  await fillDestinations();
});

const drawDst = cityMenu("dstsel", "dstbtn", "dstmenu", city => {
  dest = city;
  if (document.getElementById("setup").hidden) {   // changed from the header
    localStorage.setItem("elsewhere.dest", dest);
    savedDest = dest;
    stopDemo(); syncURL(); load();
  }
});

document.getElementById("cats").addEventListener("click", e => {
  const b = e.target.closest("button[data-cat]");
  if (!b) return;
  cat = b.dataset.cat; q = ""; committed = true;
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

document.getElementById("browsebtn").addEventListener("click", () => {
  cat = "__browse"; q = ""; committed = true;
  document.getElementById("q").value = "";
  stopDemo(); syncURL(); render();
  scrollTo({ top: 0, behavior: "smooth" });
});

document.getElementById("savedbtn").addEventListener("click", () => {
  cat = "__saved"; q = ""; committed = true;
  document.getElementById("q").value = "";
  syncURL(); render();
  scrollTo({ top: 0, behavior: "smooth" });
});

function clearView() {
  q = ""; cat = ""; committed = false;
  closeSuggest();
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
