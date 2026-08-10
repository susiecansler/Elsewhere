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

import json
import os
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response

from elsewhere import generate, links, places, verify

#: Drawn rather than exported. A favicon is seen at 16px in a tab strip more
#: often than anywhere else, and vector strokes stay crisp there where a
#: downscaled raster turns to mush. It also costs 700 bytes and no build step.
FAVICON = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
  <rect x="1" y="1" width="62" height="62" rx="15" fill="#EDF4F8"/>
  <g fill="none" stroke="#3985A6" stroke-width="5.2"
     stroke-linecap="round" stroke-linejoin="round">
    <!-- One wandering line: it sets out, doubles back on itself, then
         commits and leaves. That's the product in a single stroke. -->
    <path d="M12 51C22 51 25.5 45 23.5 39.5C22 35.2 17 35.8 17.6 40.6
             C18.4 46 25.5 45.6 31.5 41.4C38 36.9 42.5 30.5 46.2 24.6"/>
    <path d="M34.8 22.6L47.6 20.8L48.4 33.8"/>
  </g>
  <path d="M50.5 5.5C51.2 9.6 52.8 11.2 56.9 11.9C52.8 12.6 51.2 14.2 50.5 18.3
           C49.8 14.2 48.2 12.6 44.1 11.9C48.2 11.2 49.8 9.6 50.5 5.5Z"
        fill="#9CC8DC"/>
  <!-- Kept clear of the corner radius: anything past x=60 up here gets
       clipped by the rounded tile. -->
  <g stroke="#9CC8DC" stroke-width="3.2" stroke-linecap="round">
    <path d="M56 17.5L59 14.5"/>
    <path d="M57.5 24.5L60.5 23.5"/>
  </g>
</svg>
"""

#: A plain arrow for the go button. The loop is the brand and it means
#: "translate this into that" — using it twice in one sentence spends the
#: idea and leaves the reader deciding which of two identical marks is the
#: button. Submit is not a translation, it is just forward.
ARROW = (
    '<svg class="{cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
    'stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" '
    'aria-hidden="true"><path d="M4 12h15"/><path d="M13 6l6 6-6 6"/></svg>'
)

#: The mark without its tile, inlined rather than served, so it inherits
#: `currentColor` and can be dropped into a sentence, a button, or an empty
#: state without three colour variants. `{cls}` lets each use size itself.
LOOP = (
    '<svg class="{cls}" viewBox="4 2 58 54" fill="none" stroke="currentColor" '
    'stroke-width="5.2" stroke-linecap="round" stroke-linejoin="round" '
    'aria-hidden="true">'
    '<path d="M12 51C22 51 25.5 45 23.5 39.5C22 35.2 17 35.8 17.6 40.6'
    'C18.4 46 25.5 45.6 31.5 41.4C38 36.9 42.5 30.5 46.2 24.6"/>'
    '<path d="M34.8 22.6L47.6 20.8L48.4 33.8"/>'
    "</svg>"
)


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
                            "confidence": c.confidence,
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
        return page_html()

    @app.get("/api/cities")
    def api_cities() -> JSONResponse:
        """Cities that can be used as a starting point."""
        return JSONResponse(sorted(sources))

    @app.get("/api/state")
    def api_state(source: str = "") -> JSONResponse:
        return JSONResponse(state(source if source in sources else default_source))

    @app.get("/api/geo")
    def api_geo() -> JSONResponse:
        """Where each city is, so the browser can work out the nearest one.

        Sent to the client rather than resolved on the server because the
        client is the only party that knows the visitor's coordinates, and
        keeping it that way means the location never leaves the device.
        """
        centers = places.city_centers()
        return JSONResponse(
            {
                c: {"lat": centers[c][0], "lon": centers[c][1]}
                for c in sorted(sources)
                if c in centers
            }
        )

    @app.get("/favicon.svg", include_in_schema=False)
    def favicon() -> Response:
        # A week, not a year. This cached for a year on the reasoning that a
        # brand mark rarely changes — then the accent colour changed and every
        # returning visitor kept the old teal icon with no way to be told
        # otherwise. The ?v= on the link busts it now, and a shorter max-age
        # means the next change costs a week at worst.
        return Response(
            FAVICON,
            media_type="image/svg+xml",
            headers={"Cache-Control": "public, max-age=604800"},
        )

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

#: Faint line drawings in the corners, in place of the two flat discs.
#:
#: Every path is computed rather than hand-placed, which is what buys the
#: detail.
#:
#: Stroke-only, and that is the point rather than a style preference. These
#: are drawn at 16% opacity, where a 1.5px stroke disappears but a solid fill
#: still reads — so a drawing that mixes the two collapses into its blobs and
#: stops looking like anything. Every line fades together now. For the same
#: reason the head is cleared by clipping the pattern short rather than by
#: painting over it: a paper-coloured shape inside a translucent group does
#: not mask, it just composites at 16%.
#:
#: Tail spread is 1.15x the body half-height. It was 1.9x, which is a fan
#: larger than the fish and ran the top one off the canvas — the drawing was
#: literally being sliced, which is what "broken" looked like. The generator
#: prints per-fish bounds so that cannot recur silently.
#:
#: The fish are sardine forms filled with geometric pattern — scallops, a
#: stippled half against a ruled half, a diagonal lattice — and the thing
#: that makes that work is a clip path per fish. The pattern is drawn as a
#: full grid and the body outline clips it, so every motif meets the edge
#: cleanly instead of being fitted to a curve by hand. The head is a wedge
#: knocked out of the pattern so the eye has somewhere to sit.
#:
#: The balloons are built against a profile — the half-height at any
#: point along the length — so fins begin exactly on the outline, rays stop
#: where the fin membrane ends, and scale rows stay inside the body and
#: shrink toward the tail. Hand-writing this produced rays crossing the body
#: and scales stretched into wavy lines. The balloons get gores struck from
#: the crown, bands that narrow with the envelope, a scalloped hem, rigging
#: to the load ring, and a woven basket.
#:
#: Regenerate with the script in the commit history if they need changing;
#: they are pasted here so the page costs nothing at runtime.
#:
#: Fish shoal toward the top right, balloons drift up from the bottom left:
#: two ways of getting somewhere else, which is the only justification a
#: decoration needs here.
FISH = """<svg class="decor decor-fish" viewBox="0 0 380 320" fill="none"
  stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"
  aria-hidden="true">
  <g>
    <defs><clipPath id="fa"><path d="M26.0 74.0 C69.1 40.7 147.5 40.7 222.0 74.0 C147.5 107.3 69.1 107.3 26.0 74.0Z"/></clipPath></defs>
    <defs><clipPath id="fap"><rect x="77.0" y="27.4" width="196.0" height="93.3"/></clipPath></defs>
    <g clip-path="url(#fa)"><g clip-path="url(#fap)">
    <path d="M49.5 12.0 A28.7 28.7 0 0 1 49.5 69.3" fill="none" stroke-width="1.92"/>
    <path d="M49.5 37.8 A28.7 28.7 0 0 1 49.5 95.1" fill="none" stroke-width="1.92"/>
    <path d="M49.5 63.6 A28.7 28.7 0 0 1 49.5 120.9" fill="none" stroke-width="1.92"/>
    <path d="M49.5 89.4 A28.7 28.7 0 0 1 49.5 146.7" fill="none" stroke-width="1.92"/>
    <path d="M67.3 24.9 A28.7 28.7 0 0 1 67.3 82.2" fill="none" stroke-width="1.92"/>
    <path d="M67.3 50.7 A28.7 28.7 0 0 1 67.3 108.0" fill="none" stroke-width="1.92"/>
    <path d="M67.3 76.5 A28.7 28.7 0 0 1 67.3 133.8" fill="none" stroke-width="1.92"/>
    <path d="M67.3 102.3 A28.7 28.7 0 0 1 67.3 159.6" fill="none" stroke-width="1.92"/>
    <path d="M85.1 12.0 A28.7 28.7 0 0 1 85.1 69.3" fill="none" stroke-width="1.92"/>
    <path d="M85.1 37.8 A28.7 28.7 0 0 1 85.1 95.1" fill="none" stroke-width="1.92"/>
    <path d="M85.1 63.6 A28.7 28.7 0 0 1 85.1 120.9" fill="none" stroke-width="1.92"/>
    <path d="M85.1 89.4 A28.7 28.7 0 0 1 85.1 146.7" fill="none" stroke-width="1.92"/>
    <path d="M102.8 24.9 A28.7 28.7 0 0 1 102.8 82.2" fill="none" stroke-width="1.92"/>
    <path d="M102.8 50.7 A28.7 28.7 0 0 1 102.8 108.0" fill="none" stroke-width="1.92"/>
    <path d="M102.8 76.5 A28.7 28.7 0 0 1 102.8 133.8" fill="none" stroke-width="1.92"/>
    <path d="M102.8 102.3 A28.7 28.7 0 0 1 102.8 159.6" fill="none" stroke-width="1.92"/>
    <path d="M120.6 12.0 A28.7 28.7 0 0 1 120.6 69.3" fill="none" stroke-width="1.92"/>
    <path d="M120.6 37.8 A28.7 28.7 0 0 1 120.6 95.1" fill="none" stroke-width="1.92"/>
    <path d="M120.6 63.6 A28.7 28.7 0 0 1 120.6 120.9" fill="none" stroke-width="1.92"/>
    <path d="M120.6 89.4 A28.7 28.7 0 0 1 120.6 146.7" fill="none" stroke-width="1.92"/>
    <path d="M138.4 24.9 A28.7 28.7 0 0 1 138.4 82.2" fill="none" stroke-width="1.92"/>
    <path d="M138.4 50.7 A28.7 28.7 0 0 1 138.4 108.0" fill="none" stroke-width="1.92"/>
    <path d="M138.4 76.5 A28.7 28.7 0 0 1 138.4 133.8" fill="none" stroke-width="1.92"/>
    <path d="M138.4 102.3 A28.7 28.7 0 0 1 138.4 159.6" fill="none" stroke-width="1.92"/>
    <path d="M156.1 12.0 A28.7 28.7 0 0 1 156.1 69.3" fill="none" stroke-width="1.92"/>
    <path d="M156.1 37.8 A28.7 28.7 0 0 1 156.1 95.1" fill="none" stroke-width="1.92"/>
    <path d="M156.1 63.6 A28.7 28.7 0 0 1 156.1 120.9" fill="none" stroke-width="1.92"/>
    <path d="M156.1 89.4 A28.7 28.7 0 0 1 156.1 146.7" fill="none" stroke-width="1.92"/>
    <path d="M173.9 24.9 A28.7 28.7 0 0 1 173.9 82.2" fill="none" stroke-width="1.92"/>
    <path d="M173.9 50.7 A28.7 28.7 0 0 1 173.9 108.0" fill="none" stroke-width="1.92"/>
    <path d="M173.9 76.5 A28.7 28.7 0 0 1 173.9 133.8" fill="none" stroke-width="1.92"/>
    <path d="M173.9 102.3 A28.7 28.7 0 0 1 173.9 159.6" fill="none" stroke-width="1.92"/>
    <path d="M191.6 12.0 A28.7 28.7 0 0 1 191.6 69.3" fill="none" stroke-width="1.92"/>
    <path d="M191.6 37.8 A28.7 28.7 0 0 1 191.6 95.1" fill="none" stroke-width="1.92"/>
    <path d="M191.6 63.6 A28.7 28.7 0 0 1 191.6 120.9" fill="none" stroke-width="1.92"/>
    <path d="M191.6 89.4 A28.7 28.7 0 0 1 191.6 146.7" fill="none" stroke-width="1.92"/>
    <path d="M209.4 24.9 A28.7 28.7 0 0 1 209.4 82.2" fill="none" stroke-width="1.92"/>
    <path d="M209.4 50.7 A28.7 28.7 0 0 1 209.4 108.0" fill="none" stroke-width="1.92"/>
    <path d="M209.4 76.5 A28.7 28.7 0 0 1 209.4 133.8" fill="none" stroke-width="1.92"/>
    <path d="M209.4 102.3 A28.7 28.7 0 0 1 209.4 159.6" fill="none" stroke-width="1.92"/>
    <path d="M227.2 12.0 A28.7 28.7 0 0 1 227.2 69.3" fill="none" stroke-width="1.92"/>
    <path d="M227.2 37.8 A28.7 28.7 0 0 1 227.2 95.1" fill="none" stroke-width="1.92"/>
    <path d="M227.2 63.6 A28.7 28.7 0 0 1 227.2 120.9" fill="none" stroke-width="1.92"/>
    <path d="M227.2 89.4 A28.7 28.7 0 0 1 227.2 146.7" fill="none" stroke-width="1.92"/>
    <path d="M244.9 24.9 A28.7 28.7 0 0 1 244.9 82.2" fill="none" stroke-width="1.92"/>
    <path d="M244.9 50.7 A28.7 28.7 0 0 1 244.9 108.0" fill="none" stroke-width="1.92"/>
    <path d="M244.9 76.5 A28.7 28.7 0 0 1 244.9 133.8" fill="none" stroke-width="1.92"/>
    <path d="M244.9 102.3 A28.7 28.7 0 0 1 244.9 159.6" fill="none" stroke-width="1.92"/>
    </g></g>
    <path d="M65.2 45.3 L77.0 74.0 L65.2 102.7" fill="none" stroke-width="2.40"/>
    <path d="M26.0 74.0 C69.1 40.7 147.5 40.7 222.0 74.0 C147.5 107.3 69.1 107.3 26.0 74.0Z" fill="none" stroke-width="2.76"/>
    <circle cx="49.5" cy="71.3" r="11.3" fill="none" stroke-width="2.40"/>
    <circle cx="49.5" cy="71.3" r="4.8" fill="currentColor" stroke="none"/>
    <path d="M35.8 57.3 q5.9 -11.3 14.7 -4.0" stroke-width="2.16" fill="none"/>
    <defs><clipPath id="fat"><path d="M222.0 74.0 C241.6 57.3 253.4 42.3 261.2 35.7 C249.4 50.7 249.4 97.3 261.2 112.3 C253.4 105.7 241.6 90.7 222.0 74.0Z"/></clipPath></defs>
    <g clip-path="url(#fat)">
    <path d="M222.0 7.4 L265.1 19.0" stroke-width="1.68"/>
    <path d="M222.0 20.7 L265.1 32.3" stroke-width="1.68"/>
    <path d="M222.0 34.0 L265.1 45.7" stroke-width="1.68"/>
    <path d="M222.0 47.3 L265.1 59.0" stroke-width="1.68"/>
    <path d="M222.0 60.7 L265.1 72.3" stroke-width="1.68"/>
    <path d="M222.0 74.0 L265.1 85.7" stroke-width="1.68"/>
    <path d="M222.0 87.3 L265.1 99.0" stroke-width="1.68"/>
    <path d="M222.0 100.7 L265.1 112.3" stroke-width="1.68"/>
    <path d="M222.0 114.0 L265.1 125.6" stroke-width="1.68"/>
    <path d="M222.0 127.3 L265.1 139.0" stroke-width="1.68"/>
    <path d="M222.0 140.6 L265.1 152.3" stroke-width="1.68"/>
    </g>
    <path d="M222.0 74.0 C241.6 57.3 253.4 42.3 261.2 35.7 C249.4 50.7 249.4 97.3 261.2 112.3 C253.4 105.7 241.6 90.7 222.0 74.0Z" fill="none" stroke-width="2.52"/>
  </g>
  <g>
    <defs><clipPath id="fb"><path d="M104.0 168.0 C141.0 139.4 208.2 139.4 272.0 168.0 C208.2 196.6 141.0 196.6 104.0 168.0Z"/></clipPath></defs>
    <defs><clipPath id="fbp"><rect x="147.7" y="128.0" width="168.0" height="80.0"/></clipPath></defs>
    <g clip-path="url(#fb)"><g clip-path="url(#fbp)">
    <circle cx="129.2" cy="147.4" r="4.7" stroke-width="1.68"/>
    <circle cx="144.9" cy="147.4" r="4.7" stroke-width="1.68"/>
    <circle cx="160.6" cy="147.4" r="4.7" stroke-width="1.68"/>
    <circle cx="176.2" cy="147.4" r="4.7" stroke-width="1.68"/>
    <circle cx="136.8" cy="155.7" r="5.3" stroke-width="1.68"/>
    <circle cx="152.4" cy="155.7" r="5.3" stroke-width="1.68"/>
    <circle cx="168.1" cy="155.7" r="5.3" stroke-width="1.68"/>
    <circle cx="183.8" cy="155.7" r="5.3" stroke-width="1.68"/>
    <circle cx="129.2" cy="163.9" r="6.0" stroke-width="1.68"/>
    <circle cx="144.9" cy="163.9" r="6.0" stroke-width="1.68"/>
    <circle cx="160.6" cy="163.9" r="6.0" stroke-width="1.68"/>
    <circle cx="176.2" cy="163.9" r="6.0" stroke-width="1.68"/>
    <circle cx="136.8" cy="172.1" r="6.0" stroke-width="1.68"/>
    <circle cx="152.4" cy="172.1" r="6.0" stroke-width="1.68"/>
    <circle cx="168.1" cy="172.1" r="6.0" stroke-width="1.68"/>
    <circle cx="183.8" cy="172.1" r="6.0" stroke-width="1.68"/>
    <circle cx="129.2" cy="180.3" r="5.3" stroke-width="1.68"/>
    <circle cx="144.9" cy="180.3" r="5.3" stroke-width="1.68"/>
    <circle cx="160.6" cy="180.3" r="5.3" stroke-width="1.68"/>
    <circle cx="176.2" cy="180.3" r="5.3" stroke-width="1.68"/>
    <circle cx="136.8" cy="188.6" r="4.7" stroke-width="1.68"/>
    <circle cx="152.4" cy="188.6" r="4.7" stroke-width="1.68"/>
    <circle cx="168.1" cy="188.6" r="4.7" stroke-width="1.68"/>
    <circle cx="183.8" cy="188.6" r="4.7" stroke-width="1.68"/>
    <path d="M184.6 139.4 L275.4 139.4" stroke-width="1.49"/>
    <path d="M184.6 143.5 L275.4 143.5" stroke-width="1.49"/>
    <path d="M184.6 147.6 L275.4 147.6" stroke-width="1.49"/>
    <path d="M184.6 151.7 L275.4 151.7" stroke-width="1.49"/>
    <path d="M184.6 155.8 L275.4 155.8" stroke-width="1.49"/>
    <path d="M184.6 159.8 L275.4 159.8" stroke-width="1.49"/>
    <path d="M184.6 163.9 L275.4 163.9" stroke-width="1.49"/>
    <path d="M184.6 168.0 L275.4 168.0" stroke-width="1.49"/>
    <path d="M184.6 172.1 L275.4 172.1" stroke-width="1.49"/>
    <path d="M184.6 176.2 L275.4 176.2" stroke-width="1.49"/>
    <path d="M184.6 180.2 L275.4 180.2" stroke-width="1.49"/>
    <path d="M184.6 184.3 L275.4 184.3" stroke-width="1.49"/>
    <path d="M184.6 188.4 L275.4 188.4" stroke-width="1.49"/>
    <path d="M184.6 192.5 L275.4 192.5" stroke-width="1.49"/>
    <path d="M184.6 196.6 L275.4 196.6" stroke-width="1.49"/>
    <path d="M181.3 139.4 C172.9 156.6 189.7 179.4 181.3 196.6" stroke-width="2.40"/>
    </g></g>
    <path d="M137.6 143.4 L147.7 168.0 L137.6 192.6" fill="none" stroke-width="2.40"/>
    <path d="M104.0 168.0 C141.0 139.4 208.2 139.4 272.0 168.0 C208.2 196.6 141.0 196.6 104.0 168.0Z" fill="none" stroke-width="2.76"/>
    <circle cx="124.2" cy="165.7" r="9.7" fill="none" stroke-width="2.40"/>
    <circle cx="124.2" cy="165.7" r="4.1" fill="currentColor" stroke="none"/>
    <path d="M112.4 153.7 q5.0 -9.7 12.6 -3.4" stroke-width="2.16" fill="none"/>
    <defs><clipPath id="fbt"><path d="M272.0 168.0 C288.8 153.7 298.9 140.9 305.6 135.2 C295.5 148.0 295.5 188.0 305.6 200.8 C298.9 195.1 288.8 182.3 272.0 168.0Z"/></clipPath></defs>
    <g clip-path="url(#fbt)">
    <path d="M272.0 110.9 L309.0 120.9" stroke-width="1.68"/>
    <path d="M272.0 122.3 L309.0 132.3" stroke-width="1.68"/>
    <path d="M272.0 133.7 L309.0 143.7" stroke-width="1.68"/>
    <path d="M272.0 145.2 L309.0 155.1" stroke-width="1.68"/>
    <path d="M272.0 156.6 L309.0 166.6" stroke-width="1.68"/>
    <path d="M272.0 168.0 L309.0 178.0" stroke-width="1.68"/>
    <path d="M272.0 179.4 L309.0 189.4" stroke-width="1.68"/>
    <path d="M272.0 190.8 L309.0 200.8" stroke-width="1.68"/>
    <path d="M272.0 202.3 L309.0 212.3" stroke-width="1.68"/>
    <path d="M272.0 213.7 L309.0 223.7" stroke-width="1.68"/>
    <path d="M272.0 225.1 L309.0 235.1" stroke-width="1.68"/>
    </g>
    <path d="M272.0 168.0 C288.8 153.7 298.9 140.9 305.6 135.2 C295.5 148.0 295.5 188.0 305.6 200.8 C298.9 195.1 288.8 182.3 272.0 168.0Z" fill="none" stroke-width="2.52"/>
  </g>
  <g>
    <defs><clipPath id="fc"><path d="M44.0 254.0 C74.8 230.2 130.8 230.2 184.0 254.0 C130.8 277.8 74.8 277.8 44.0 254.0Z"/></clipPath></defs>
    <defs><clipPath id="fcp"><rect x="80.4" y="220.7" width="140.0" height="66.6"/></clipPath></defs>
    <g clip-path="url(#fc)"><g clip-path="url(#fcp)">
    <path d="M20.2 225.4 L77.3 282.6" stroke-width="1.80"/>
    <path d="M20.2 282.6 L77.3 225.4" stroke-width="1.80"/>
    <path d="M31.1 225.4 L88.3 282.6" stroke-width="1.80"/>
    <path d="M31.1 282.6 L88.3 225.4" stroke-width="1.80"/>
    <path d="M42.1 225.4 L99.2 282.6" stroke-width="1.80"/>
    <path d="M42.1 282.6 L99.2 225.4" stroke-width="1.80"/>
    <path d="M53.0 225.4 L110.2 282.6" stroke-width="1.80"/>
    <path d="M53.0 282.6 L110.2 225.4" stroke-width="1.80"/>
    <path d="M64.0 225.4 L121.1 282.6" stroke-width="1.80"/>
    <path d="M64.0 282.6 L121.1 225.4" stroke-width="1.80"/>
    <path d="M74.9 225.4 L132.1 282.6" stroke-width="1.80"/>
    <path d="M74.9 282.6 L132.1 225.4" stroke-width="1.80"/>
    <path d="M85.9 225.4 L143.0 282.6" stroke-width="1.80"/>
    <path d="M85.9 282.6 L143.0 225.4" stroke-width="1.80"/>
    <path d="M96.8 225.4 L154.0 282.6" stroke-width="1.80"/>
    <path d="M96.8 282.6 L154.0 225.4" stroke-width="1.80"/>
    <path d="M107.8 225.4 L164.9 282.6" stroke-width="1.80"/>
    <path d="M107.8 282.6 L164.9 225.4" stroke-width="1.80"/>
    <path d="M118.7 225.4 L175.9 282.6" stroke-width="1.80"/>
    <path d="M118.7 282.6 L175.9 225.4" stroke-width="1.80"/>
    <path d="M129.7 225.4 L186.8 282.6" stroke-width="1.80"/>
    <path d="M129.7 282.6 L186.8 225.4" stroke-width="1.80"/>
    <path d="M140.6 225.4 L197.7 282.6" stroke-width="1.80"/>
    <path d="M140.6 282.6 L197.7 225.4" stroke-width="1.80"/>
    <path d="M151.6 225.4 L208.7 282.6" stroke-width="1.80"/>
    <path d="M151.6 282.6 L208.7 225.4" stroke-width="1.80"/>
    <path d="M162.5 225.4 L219.6 282.6" stroke-width="1.80"/>
    <path d="M162.5 282.6 L219.6 225.4" stroke-width="1.80"/>
    <path d="M173.5 225.4 L230.6 282.6" stroke-width="1.80"/>
    <path d="M173.5 282.6 L230.6 225.4" stroke-width="1.80"/>
    <path d="M184.4 225.4 L241.5 282.6" stroke-width="1.80"/>
    <path d="M184.4 282.6 L241.5 225.4" stroke-width="1.80"/>
    <path d="M195.4 225.4 L252.5 282.6" stroke-width="1.80"/>
    <path d="M195.4 282.6 L252.5 225.4" stroke-width="1.80"/>
    <path d="M206.3 225.4 L263.4 282.6" stroke-width="1.80"/>
    <path d="M206.3 282.6 L263.4 225.4" stroke-width="1.80"/>
    <path d="M217.3 225.4 L274.4 282.6" stroke-width="1.80"/>
    <path d="M217.3 282.6 L274.4 225.4" stroke-width="1.80"/>
    </g></g>
    <path d="M72.0 233.5 L80.4 254.0 L72.0 274.5" fill="none" stroke-width="2.40"/>
    <path d="M44.0 254.0 C74.8 230.2 130.8 230.2 184.0 254.0 C130.8 277.8 74.8 277.8 44.0 254.0Z" fill="none" stroke-width="2.76"/>
    <circle cx="60.8" cy="252.1" r="8.1" fill="none" stroke-width="2.40"/>
    <circle cx="60.8" cy="252.1" r="3.4" fill="currentColor" stroke="none"/>
    <path d="M51.0 242.1 q4.2 -8.1 10.5 -2.9" stroke-width="2.16" fill="none"/>
    <defs><clipPath id="fct"><path d="M184.0 254.0 C198.0 242.1 206.4 231.4 212.0 226.6 C203.6 237.3 203.6 270.7 212.0 281.4 C206.4 276.6 198.0 265.9 184.0 254.0Z"/></clipPath></defs>
    <g clip-path="url(#fct)">
    <path d="M184.0 206.4 L214.8 214.7" stroke-width="1.68"/>
    <path d="M184.0 215.9 L214.8 224.3" stroke-width="1.68"/>
    <path d="M184.0 225.4 L214.8 233.8" stroke-width="1.68"/>
    <path d="M184.0 235.0 L214.8 243.3" stroke-width="1.68"/>
    <path d="M184.0 244.5 L214.8 252.8" stroke-width="1.68"/>
    <path d="M184.0 254.0 L214.8 262.3" stroke-width="1.68"/>
    <path d="M184.0 263.5 L214.8 271.8" stroke-width="1.68"/>
    <path d="M184.0 273.0 L214.8 281.4" stroke-width="1.68"/>
    <path d="M184.0 282.6 L214.8 290.9" stroke-width="1.68"/>
    <path d="M184.0 292.1 L214.8 300.4" stroke-width="1.68"/>
    <path d="M184.0 301.6 L214.8 309.9" stroke-width="1.68"/>
    </g>
    <path d="M184.0 254.0 C198.0 242.1 206.4 231.4 212.0 226.6 C203.6 237.3 203.6 270.7 212.0 281.4 C206.4 276.6 198.0 265.9 184.0 254.0Z" fill="none" stroke-width="2.52"/>
  </g>
</svg>
"""

BALLOONS = """<svg class="decor decor-balloons" viewBox="0 0 280 340" fill="none"
  stroke="currentColor" stroke-width="1.5" stroke-linecap="round"
  stroke-linejoin="round" aria-hidden="true">
  <g>
    <path d="M92.0 28.0 C144.8 32.6 133.6 74.4 106.4 94.8 L77.6 94.8 C50.4 74.4 39.2 32.6 92.0 28.0Z"/>
    <path d="M92.0 28.0 C54.0 37.3 62.0 74.4 81.6 94.8" stroke-width="0.75"/>
    <path d="M92.0 28.0 C68.2 37.3 73.3 74.4 85.5 94.8" stroke-width="0.75"/>
    <path d="M92.0 28.0 C81.4 37.3 83.7 74.4 89.1 94.8" stroke-width="0.75"/>
    <path d="M92.0 28.0 C92.0 37.3 92.0 74.4 92.0 94.8" stroke-width="0.75"/>
    <path d="M92.0 28.0 C102.6 37.3 100.3 74.4 94.9 94.8" stroke-width="0.75"/>
    <path d="M92.0 28.0 C115.8 37.3 110.7 74.4 98.5 94.8" stroke-width="0.75"/>
    <path d="M92.0 28.0 C130.0 37.3 122.0 74.4 102.4 94.8" stroke-width="0.75"/>
    <path d="M47.3 48.4 Q92.0 53.5 136.7 48.4" stroke-width="0.68"/>
    <path d="M56.8 65.1 Q92.0 70.2 127.2 65.1" stroke-width="0.68"/>
    <path d="M69.1 81.8 Q92.0 86.9 114.9 81.8" stroke-width="0.68"/>
    <path d="M77.6 94.8 Q79.7 97.4 81.7 94.8 M81.7 94.8 Q83.8 97.4 85.8 94.8 M85.8 94.8 Q87.9 97.4 89.9 94.8 M89.9 94.8 Q92.0 97.4 94.1 94.8 M94.1 94.8 Q96.1 97.4 98.2 94.8 M98.2 94.8 Q100.2 97.4 102.3 94.8 M102.3 94.8 Q104.3 97.4 106.4 94.8" stroke-width="0.68"/>
    <path d="M78.4 95.9 L79.2 109.7" stroke-width="0.68"/>
    <path d="M86.6 95.9 L86.9 109.7" stroke-width="0.68"/>
    <path d="M97.4 95.9 L97.1 109.7" stroke-width="0.68"/>
    <path d="M105.6 95.9 L104.8 109.7" stroke-width="0.68"/>
    <path d="M79.2 109.7 L104.8 109.7 L103.0 120.3 L81.0 120.3Z"/>
    <path d="M79.6 113.3 L104.4 113.3" stroke-width="0.60"/>
    <path d="M80.1 116.8 L103.9 116.8" stroke-width="0.60"/>
    <path d="M83.6 109.7 L83.6 120.3" stroke-width="0.52"/>
    <path d="M89.2 109.7 L89.2 120.3" stroke-width="0.52"/>
    <path d="M94.8 109.7 L94.8 120.3" stroke-width="0.52"/>
    <path d="M100.4 109.7 L100.4 120.3" stroke-width="0.52"/>
  </g>
  <g>
    <path d="M208.0 138.0 C243.6 141.1 236.1 169.3 217.7 183.1 L198.3 183.1 C179.9 169.3 172.4 141.1 208.0 138.0Z"/>
    <path d="M208.0 138.0 C182.3 144.3 187.8 169.3 201.0 183.1" stroke-width="0.75"/>
    <path d="M208.0 138.0 C192.0 144.3 195.4 169.3 203.6 183.1" stroke-width="0.75"/>
    <path d="M208.0 138.0 C200.9 144.3 202.4 169.3 206.1 183.1" stroke-width="0.75"/>
    <path d="M208.0 138.0 C208.0 144.3 208.0 169.3 208.0 183.1" stroke-width="0.75"/>
    <path d="M208.0 138.0 C215.1 144.3 213.6 169.3 209.9 183.1" stroke-width="0.75"/>
    <path d="M208.0 138.0 C224.0 144.3 220.6 169.3 212.4 183.1" stroke-width="0.75"/>
    <path d="M208.0 138.0 C233.7 144.3 228.2 169.3 215.0 183.1" stroke-width="0.75"/>
    <path d="M177.8 151.8 Q208.0 155.2 238.2 151.8" stroke-width="0.68"/>
    <path d="M184.2 163.1 Q208.0 166.5 231.8 163.1" stroke-width="0.68"/>
    <path d="M192.6 174.3 Q208.0 177.8 223.4 174.3" stroke-width="0.68"/>
    <path d="M198.3 183.1 Q199.7 184.9 201.1 183.1 M201.1 183.1 Q202.4 184.9 203.8 183.1 M203.8 183.1 Q205.2 184.9 206.6 183.1 M206.6 183.1 Q208.0 184.9 209.4 183.1 M209.4 183.1 Q210.8 184.9 212.2 183.1 M212.2 183.1 Q213.6 184.9 214.9 183.1 M214.9 183.1 Q216.3 184.9 217.7 183.1" stroke-width="0.68"/>
    <path d="M198.8 183.9 L199.4 193.1" stroke-width="0.68"/>
    <path d="M204.3 183.9 L204.5 193.1" stroke-width="0.68"/>
    <path d="M211.7 183.9 L211.5 193.1" stroke-width="0.68"/>
    <path d="M217.2 183.9 L216.6 193.1" stroke-width="0.68"/>
    <path d="M199.4 193.1 L216.6 193.1 L215.4 200.3 L200.6 200.3Z"/>
    <path d="M199.6 195.6 L216.4 195.6" stroke-width="0.60"/>
    <path d="M200.0 197.9 L216.0 197.9" stroke-width="0.60"/>
    <path d="M202.3 193.1 L202.3 200.3" stroke-width="0.52"/>
    <path d="M206.1 193.1 L206.1 200.3" stroke-width="0.52"/>
    <path d="M209.9 193.1 L209.9 200.3" stroke-width="0.52"/>
    <path d="M213.7 193.1 L213.7 200.3" stroke-width="0.52"/>
  </g>
  <g>
    <path d="M48.0 224.0 C75.7 226.4 69.8 248.4 55.6 259.1 L40.4 259.1 C26.2 248.4 20.3 226.4 48.0 224.0Z"/>
    <path d="M48.0 224.0 C28.0 228.9 32.3 248.4 42.6 259.1" stroke-width="0.75"/>
    <path d="M48.0 224.0 C35.5 228.9 38.2 248.4 44.6 259.1" stroke-width="0.75"/>
    <path d="M48.0 224.0 C42.5 228.9 43.6 248.4 46.5 259.1" stroke-width="0.75"/>
    <path d="M48.0 224.0 C48.0 228.9 48.0 248.4 48.0 259.1" stroke-width="0.75"/>
    <path d="M48.0 224.0 C53.5 228.9 52.4 248.4 49.5 259.1" stroke-width="0.75"/>
    <path d="M48.0 224.0 C60.5 228.9 57.8 248.4 51.4 259.1" stroke-width="0.75"/>
    <path d="M48.0 224.0 C68.0 228.9 63.7 248.4 53.4 259.1" stroke-width="0.75"/>
    <path d="M24.5 234.7 Q48.0 237.4 71.5 234.7" stroke-width="0.68"/>
    <path d="M29.5 243.5 Q48.0 246.2 66.5 243.5" stroke-width="0.68"/>
    <path d="M36.0 252.3 Q48.0 254.9 60.0 252.3" stroke-width="0.68"/>
    <path d="M40.4 259.1 Q41.5 260.4 42.6 259.1 M42.6 259.1 Q43.7 260.4 44.8 259.1 M44.8 259.1 Q45.8 260.4 46.9 259.1 M46.9 259.1 Q48.0 260.4 49.1 259.1 M49.1 259.1 Q50.2 260.4 51.2 259.1 M51.2 259.1 Q52.3 260.4 53.4 259.1 M53.4 259.1 Q54.5 260.4 55.6 259.1" stroke-width="0.68"/>
    <path d="M40.9 259.7 L41.3 266.9" stroke-width="0.68"/>
    <path d="M45.1 259.7 L45.3 266.9" stroke-width="0.68"/>
    <path d="M50.9 259.7 L50.7 266.9" stroke-width="0.68"/>
    <path d="M55.1 259.7 L54.7 266.9" stroke-width="0.68"/>
    <path d="M41.3 266.9 L54.7 266.9 L53.8 272.5 L42.2 272.5Z"/>
    <path d="M41.5 268.8 L54.5 268.8" stroke-width="0.60"/>
    <path d="M41.8 270.6 L54.2 270.6" stroke-width="0.60"/>
    <path d="M43.6 266.9 L43.6 272.5" stroke-width="0.52"/>
    <path d="M46.5 266.9 L46.5 272.5" stroke-width="0.52"/>
    <path d="M49.5 266.9 L49.5 272.5" stroke-width="0.52"/>
    <path d="M52.4 266.9 L52.4 272.5" stroke-width="0.52"/>
  </g>
  <g>
    <path d="M190.0 56.0 q4.5 -3.8 9.0 0 q4.5 -3.8 9.0 0" stroke-width="0.9"/>
    <path d="M205.0 48.0 q4.5 -3.8 9.0 0 q4.5 -3.8 9.0 0" stroke-width="0.9"/>
    <path d="M220.0 56.0 q4.5 -3.8 9.0 0 q4.5 -3.8 9.0 0" stroke-width="0.9"/>
  </g>
</svg>
"""


DECOR = FISH + BALLOONS


def supabase_config() -> dict[str, str]:
    """Credentials for the accounts backend, or empty if it isn't set up.

    The anon key is meant to be public — it identifies the project, and the
    row-level security policies in data/supabase/schema.sql are what actually
    protect the data. The service-role key is the dangerous one and must
    never appear here or anywhere else the browser can reach.

    Absent config is a supported state, not an error: verification simply
    doesn't appear, and every other part of the site works as before.
    """
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_ANON_KEY", "").strip()
    return {"url": url, "key": key} if url and key else {}


def _decor(prefix: str) -> str:
    """One copy of the corner illustrations, with its ids namespaced.

    The page renders the illustrations twice — once on the city picker, once
    on the search screen — and both copies carried the same clipPath ids
    (`fa`, `fb`, `fc`...). Ids must be unique in a document: `url(#fa)`
    resolves to whichever element comes first, so the *visible* copy on the
    search screen was being clipped by definitions living inside the hidden
    picker. With that subtree display:none the clip resolves to nothing, the
    pattern grids render unclipped, and what you get is a mass of scallops
    and stripes across the corner instead of three fish.

    Namespacing per copy fixes it at the source. Nothing about the artwork,
    its size, or its position changes.
    """
    out = DECOR
    for name in ("fa", "fb", "fc"):
        for suffix in ("", "p", "t"):
            old = name + suffix
            out = out.replace(f'id="{old}"', f'id="{prefix}{old}"')
            out = out.replace(f"url(#{old})", f"url(#{prefix}{old})")
    return out


def page_html() -> str:
    """The page with the mark substituted into every slot that wants it."""
    return (
        PAGE.replace("__LOOP_INLINE__", LOOP.format(cls="loop"))
        .replace("__LOOP_NEXT__", ARROW.format(cls="go-arrow"))
        .replace("__LOOP_EMPTY__", LOOP.format(cls="loop-empty"))
        .replace("__LOOP_SPIN__", LOOP.format(cls="loop-spin"))
        .replace("__SUPABASE__", json.dumps(supabase_config()))
        # Once per screen, each with its own id namespace.
        .replace("__DECOR__", _decor("s-"), 1)
        .replace("__DECOR__", _decor("q-"), 1)
    )


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Elsewhere — every city has an H-E-B</title>
<link rel="icon" type="image/svg+xml" href="/favicon.svg?v=2">
<meta name="theme-color" content="#FFFFFF">
<meta name="description" content="Name a place you love and find its counterpart in another city — matched by the role it plays in local life, not by category.">
<style>
/* ─── Design system ───────────────────────────────────────────────────────
   Audited before it was written. The stylesheet had ten border radii,
   twenty-eight font sizes, six weights, five transition durations and
   fifty-eight distinct padding values — colour was the only part with a
   system, which is exactly why the page read as assembled rather than
   designed.
   
   Everything below is a scale. The rules that follow may only use these
   tokens; any new value should become one or resolve to one.
   
   The character comes from restraint plus one hot colour: warm mint-white
   ground, near-black ink with a green cast, a single blue-teal accent, and
   pale mint as the only tint. No gradients, no glass, no second accent. */
:root {
  color-scheme: light;

  /* Ground. Mint-tinted rather than pure white — a page that is #FFF from
     edge to edge reads as software. Cards are white so they lift off it
     without needing a shadow to prove it. */
  --paper:  #F7FAF8;
  --panel:  #FFFFFF;
  --sunk:   #EFF4F1;

  /* Ink, with the same green cast as the ground so nothing looks pasted on. */
  --ink:    #16201F;   /* 15.9:1 on ground */
  --dim:    #5A6A67;   /* 5.4:1  — body and secondary text */
  --faint:  #93A29E;   /* 2.5:1  — non-essential only: placeholders, credits */
  --hair:   #E2EAE6;
  --hair-2: #D3DEDA;

  /* One accent. --accent is the brand blue-teal; --accent-deep is the same
     hue taken down until small white text on it and it on the ground both
     clear AA (5.5:1 and 5.8:1). Fills and small links use deep. */
  --accent:      #3985A6;
  --accent-deep: #2E6C86;
  --accent-soft: #E8F1F5;
  --on-accent:   #FFFFFF;

  /* Pale mint, the only tint. Chips, wells, quiet fills. */
  --mint:      #E6F3EC;
  --mint-deep: #CFE6DA;

  /* Type: six sizes on a 1.25 ratio, three weights. Editorial comes from the
     jump between display and metadata, not from more styles. */
  --t-xs: 12px; --t-sm: 14px; --t-md: 16px; --t-lg: 20px; --t-xl: 28px;
  --t-display: clamp(32px, 4.6vw, 52px);
  --w-body: 400; --w-label: 500; --w-display: 700;

  /* Space: 4px base. Every padding, margin and gap resolves to one of six. */
  --s1: 4px; --s2: 8px; --s3: 12px; --s4: 20px; --s5: 32px; --s6: 56px;

  /* Radius: three. Controls, surfaces, pills. Nothing in between. */
  --r-sm: 8px; --r-md: 20px; --r-pill: 999px;

  /* Elevation: a hairline at rest, one soft shadow on lift. Nothing floats
     by default. */
  --e-hair: inset 0 0 0 1px var(--hair);
  --e-soft: 0 2px 8px rgba(22,32,31,.06), 0 12px 28px -12px rgba(22,32,31,.14);
  --e-lift: 0 4px 12px rgba(22,32,31,.08), 0 20px 44px -16px rgba(22,32,31,.20);

  /* Motion: fast for state, slow for entrance, one curve. */
  --t-fast: 150ms; --t-slow: 300ms;
  --ease: cubic-bezier(.2, .7, .3, 1);

  /* Type families. */
  --sans: ui-sans-serif, -apple-system, "Segoe UI", Inter, Roboto, "Helvetica Neue", sans-serif;
  --mono: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  --display: var(--sans);

  /* ── Aliases ──────────────────────────────────────────────────────────
     Names the rest of the sheet already uses, pointed at the scale above so
     one definition governs. New rules should use the tokens, not these. */
  --shadow: var(--e-soft);
  --lift: var(--e-lift);
  --hard: var(--e-lift); --hard-sm: var(--e-soft);
  --spring: var(--ease);
  --chip: var(--mint);
  --pink: var(--accent); --pink-deep: var(--accent-deep); --on-pink: var(--on-accent);
  --turquoise: var(--accent);
  --gold: #E9C46A; --emerald: var(--accent);
  --bar: var(--panel); --bar-ink: var(--ink); --bar-line: var(--hair-2);
  --bar-dim: var(--dim); --bar-field: var(--panel);
}

:root[data-theme="dark"] {
  color-scheme: dark;
  --paper: #101413; --panel: #171D1C; --sunk: #1E2624;
  --ink: #EDF3F0; --dim: #A9B6B2; --faint: #75837F;
  --hair: #26302E; --hair-2: #33403D;
  --accent: #6BB6D4; --accent-deep: #8CC9E0; --accent-soft: #17272F;
  --on-accent: #10201E;
  --mint: #1B2A24; --mint-deep: #24382F;
  --e-hair: inset 0 0 0 1px var(--hair);
  --e-soft: 0 2px 8px rgba(0,0,0,.5), 0 12px 28px -12px rgba(0,0,0,.6);
  --e-lift: 0 4px 12px rgba(0,0,0,.55), 0 20px 44px -16px rgba(0,0,0,.7);
  --gold: #E9C46A;
}
* { box-sizing: border-box; }
/* Nothing had a visible focus ring, so the whole app was unusable by
   keyboard. :focus-visible keeps it off for mouse users. */
:focus-visible {
  outline: 2px solid var(--accent); outline-offset: 3px; border-radius: var(--r-sm);
}
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
  padding: 12px 14px; border-radius: var(--r-pill);
}
.bar .chip:hover { background: var(--sunk); transform: none; }
.bar .chip:active { transform: none; }
/* The city pair outranks the other header controls — it is the state
   everything else on the page is relative to — so it wears the accent while
   Browse and Saved stay plain text. A slightly heavier outline and the
   accent colour do that without adding a filled button to the bar. */
#pairbtn {
  box-shadow: inset 0 0 0 1.5px var(--accent); padding: 11px 16px;
  color: var(--accent-deep);
}
#pairbtn:hover { box-shadow: inset 0 0 0 1.5px var(--accent-deep); background: var(--accent-soft); }
#pairbtn:active { background: var(--mint); }
.bar #controls .field input { background: var(--bar-field); color: var(--bar-ink); box-shadow: none; }
.bar #controls .field input::placeholder { color: var(--bar-dim); }
.bar #controls .field input:focus { box-shadow: 0 0 0 2px var(--gold); }
#barslot { display: flex; gap: 10px; align-items: center; flex: 1; min-width: 0; }
/* A search field stretched across a 1500px header reads as a text area, not
   a search box. */
#barslot .field { max-width: 560px; }
#browsebtn { margin-left: auto; }
#savedbtn { margin-left: 0; }
#theme { padding: 9px 12px; font-size: var(--t-md); line-height: 1; }

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
  background: var(--panel); border-radius: var(--r-pill); padding: 6px 6px 6px 8px;
  box-shadow: 0 1px 2px rgba(0,0,0,.08), 0 8px 28px rgba(0,0,0,.10);
  max-width: 780px; margin: 0 auto; width: 100%;
}
.stage #controls:hover, .setup #controls:hover {
  box-shadow: 0 2px 4px rgba(0,0,0,.10), 0 14px 40px rgba(0,0,0,.14);
}
input[type=search], input[type=text], select {
  font: inherit; font-size: var(--t-md); padding: 9px 16px; border: 0;
  border-radius: var(--r-pill); background: var(--panel); color: var(--ink);
  box-shadow: var(--shadow); transition: box-shadow var(--t-fast), transform var(--t-fast) var(--ease);
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
/* Deliberately no z-index here. A positioned ancestor with one creates a
   stacking context, which traps the menu's z-index inside it — the menu was
   losing to a plain button that came later in the document, despite asking
   for 200. Leaving this at auto lets the menu compete in the page's own
   context, where it wins. */
.citypick { position: relative; }
.citybtn {
  display: inline-flex; align-items: center; gap: 9px;
  font-size: var(--t-md); font-weight: var(--w-display); letter-spacing: -0.01em;
  padding: 9px 16px; background: var(--panel); color: var(--ink);
  box-shadow: var(--shadow);
}
.citybtn:hover { box-shadow: var(--lift); }
.citybtn .caret { font-size: var(--t-xs); color: var(--faint); transition: transform var(--t-fast) var(--ease); }
.citybtn[aria-expanded=true] .caret { transform: rotate(180deg); }
/* The menu outranks everything around it. It sits inside the sentence, which
   has a button immediately after it and another below, and a dropdown that
   loses to either is worse than no dropdown. */
.citymenu {
  position: absolute; top: calc(100% + 8px); left: 0; z-index: 200;
  min-width: 100%; padding: 7px; border-radius: var(--r-md);
  background: var(--panel); box-shadow: var(--lift);
  display: flex; flex-direction: column; gap: 2px;
  transform-origin: top center;
  animation: menuin .18s var(--ease);
}
.citymenu[hidden] { display: none; }
/* Flipped when the viewport has no room underneath — see openCityMenu. */
.citymenu.up { top: auto; bottom: calc(100% + 8px); transform-origin: bottom center; }
@keyframes menuin { from { opacity: 0; transform: translateY(-6px) scale(.97); } }
.citymenu button {
  text-align: left; white-space: nowrap; padding: 10px 16px; border-radius: var(--r-sm);
  font-size: var(--t-md); font-weight: var(--w-label); background: none; color: var(--ink);
}
.citymenu button:hover { background: var(--chip); }
.citymenu button[aria-selected=true] { background: var(--pink-deep); color: var(--on-pink); }
.from, .to { font-size: var(--t-sm); color: var(--dim); font-weight: var(--w-label); white-space: nowrap; }
.to { color: var(--dim); }
.field { position: relative; display: flex; flex: 1; min-width: 0; }
.field input[type=search] { width: 100%; padding-right: 42px; }
.field .clear {
  position: absolute; right: 7px; top: 50%; transform: translateY(-50%);
  width: 28px; height: 28px; background: var(--chip); color: var(--accent-deep);
  font-size: var(--t-md); line-height: 1; display: grid; place-items: center;
}
.field .clear[hidden] { display: none; }
/* Typing a place name exactly is a memory test nobody signed up for —
   "Torchys", "torchy's" and "Alamo" should all get you there. */
.suggest {
  position: absolute; top: calc(100% + 10px); left: 0; right: 0; z-index: 60;
  padding: 8px; border-radius: var(--r-md); background: var(--panel);
  box-shadow: var(--lift), inset 0 0 0 1px var(--hair);
  max-height: 340px; overflow-y: auto; text-align: left;
}
.suggest[hidden] { display: none; }
.suggest button {
  display: block; width: 100%; text-align: left; padding: 10px 14px;
  border-radius: var(--r-sm); background: none; color: var(--ink);
  font-size: var(--t-md); font-weight: var(--w-label);
}
.suggest button .cat { font-size: var(--t-sm); font-weight: var(--w-body); color: var(--faint); margin-left: 10px; }
.suggest button:hover { background: var(--sunk); }
.suggest button.on { background: var(--accent); color: var(--on-accent); }
.suggest button.on .cat { color: var(--on-accent); opacity: .85; }
.field .clear:hover { background: var(--pink-deep); color: var(--on-pink); }

button {
  font: inherit; border: 0; border-radius: var(--r-pill); cursor: pointer;
  transition: transform var(--t-fast) var(--ease), box-shadow var(--t-fast), color var(--t-fast), background var(--t-fast);
}
.chip {
  font-size: var(--t-sm); font-weight: var(--w-label); padding: 11px 16px;
  background: var(--panel); color: var(--ink); box-shadow: inset 0 0 0 1px var(--hair-2);
}
.chip:hover { color: var(--ink); background: var(--sunk); }
/* Press moves *into* the shadow, so the button behaves like a physical key. */
.chip:active { background: var(--mint); }
/* The brand is the way home, so it should look like the biggest thing in the
   bar rather than a label sharing its baseline with the controls. */
button.brand {
  background: none; margin: 0; padding: 0 20px 0 0;
  display: inline-flex; align-items: center; gap: 9px;
  font-size: var(--t-xl); font-weight: var(--w-display); letter-spacing: -0.035em;
  color: var(--accent); line-height: 1; align-self: center;
}
button.brand:hover { color: var(--accent-deep); }
/* The tile's cream ground is invisible on white and muddy on the dark
   theme, so the mark wears the same rounding as the rest of the chrome and
   picks up a faint edge to sit on. */
button.brand img { border-radius: var(--r-sm); box-shadow: inset 0 0 0 1px var(--hair); }
:root[data-theme="dark"] button.brand img { opacity: .95; }
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

.stage .inner, .stage .tryfoot { position: relative; z-index: 1; }
.stage[hidden] { display: none; }
.stage .inner { width: 100%; max-width: min(900px, 92vw); margin: 0 auto; }
.stage #controls { justify-content: center; flex: 0 1 auto; }
.stage #controls .field { flex: 1 1 auto; max-width: none; }
.stage #controls input[type=search] {
  font-size: var(--t-md); padding: 18px 22px; background: none; box-shadow: none;
}
.stage #controls input[type=search]:focus { box-shadow: none; }
.stage .q { font-size: var(--t-md); font-weight: var(--w-label); color: var(--dim); margin: 40px 0 0; }

/* The search itself gets the round button, matching Next on step one. */
.field .go {
  width: 48px; height: 48px; flex: 0 0 48px; border-radius: var(--r-pill);
  background: var(--accent); color: #fff; display: grid; place-items: center;
  font-size: var(--t-lg); margin-left: 4px;
}
.field .go:hover { background: var(--accent-deep); }

.stage.typing {
  min-height: 0; overflow: visible; padding-bottom: 26px; justify-content: flex-start;
  padding-top: 28px;
}
.stage.typing .decor { display: none; }
.stage.typing .ask { font-size: clamp(20px, 2.4vw, 28px); margin-bottom: 20px; }

.tryfoot .q { font-size: var(--t-sm); font-weight: var(--w-label); color: var(--dim); margin: 0 0 14px; }
.peekcity { font-size: var(--t-sm); color: var(--faint); margin-right: 8px; }
.peekrow { font-size: var(--t-md); color: var(--dim); }
.peekrow b { font-weight: var(--w-label); font-size: var(--t-md); color: var(--ink); }

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
  transition: transform var(--t-slow) cubic-bezier(.4, 0, .2, 1);
}
.rail.hold .track { transition: none; }   /* the seamless wrap, unanimated */
.eg {
  font-size: var(--t-sm); font-weight: var(--w-label); padding: 9px 16px; white-space: nowrap;
  background: var(--chip); color: var(--accent-deep);
}
/* Alternating between the two brand colours at low strength, rather than
   introducing two more hues purely for decoration. */
.eg:nth-child(3n+2) { background: color-mix(in srgb, var(--pink) 15%, var(--panel)); color: var(--pink-deep); }
.eg:nth-child(3n+3) { background: color-mix(in srgb, var(--accent) 15%, var(--panel)); color: var(--accent-deep); }
.eg:hover { background: var(--pink-deep); color: var(--on-pink); transform: translateY(-2px); }

.browse { padding: 56px 40px 60px; text-align: left; max-width: 1180px; margin: 0 auto; }
.browse[hidden] { display: none; }
.browseh {
  font-size: clamp(24px, 3vw, 34px); font-weight: var(--w-display); letter-spacing: -0.03em;
  margin: 0 0 28px; color: var(--ink);
}
.cats { max-width: 900px; margin: 0 auto; }
.cats {
  display: grid; gap: 12px; max-width: none; margin: 0;
  grid-template-columns: repeat(auto-fill, minmax(210px, 1fr));
}
.cicon {
  width: 34px; height: 34px; color: var(--accent); margin-bottom: 12px;
  transition: transform var(--t-fast) var(--ease);
}
.cats button:hover .cicon { transform: translateY(-2px) rotate(-3deg); }
.clabel { display: block; }
.cats button {
  font-size: var(--t-md); font-weight: var(--w-label); padding: 20px 22px 22px; text-align: left;
  background: var(--panel); color: var(--ink); border-radius: var(--r-md);
  box-shadow: inset 0 0 0 1px var(--hair);
  display: flex; flex-direction: column; align-items: flex-start; gap: 4px;
}
.cats button:hover { box-shadow: var(--e-lift); }
.cats button:active { transform: none; }
.cats button .n { font-size: var(--t-sm); font-weight: var(--w-body); color: var(--dim); margin: 0; }

/* ─── Results ─────────────────────────────────────────────────────────── */
main { max-width: 860px; margin: 0 auto; padding: 20px 24px 140px; }
.crumb {
  max-width: 1180px; margin: 0 auto; padding: 26px 40px 0;
  display: flex; align-items: baseline; gap: 14px;
}
.crumb[hidden] { display: none; }
.crumb span { font-size: var(--t-md); font-weight: var(--w-label); color: var(--ink); }
/* Listings, not a newspaper index. Each answer is a card: the map reads as
   the photo, the name as the title, the roles as the amenity line. Cards say
   "these are comparable things, pick one", which is exactly the job. */
main { max-width: 1180px; margin: 0 auto; padding: 28px 40px 140px; }
.card {
  background: var(--panel); border-radius: var(--r-md); padding: 0; margin: 0 0 8px;
  box-shadow: none; display: block;
  transition: none;
}
.card:hover { box-shadow: none; transform: none; }
/* One hover rule for the whole site: colour and elevation may change,
   geometry may not. Half the hovers used to nudge the element, which is what
   made the page feel fidgety under the cursor. */
.card > .head {
  display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap;
  padding: 26px 4px 14px; margin: 0; box-shadow: none;
}
.head h2 {
  font-size: var(--t-md); font-weight: var(--w-label); letter-spacing: -0.01em;
  margin: 0; color: var(--dim); line-height: 1.2;
}
.arrow { font-size: var(--t-md); color: var(--faint); }
.roles { display: flex; flex-wrap: wrap; gap: 6px; width: 100%; margin-top: 4px; }
.role {
  font-size: var(--t-xs); font-weight: var(--w-label); color: var(--dim);
  padding: 5px 10px; border-radius: var(--r-pill); background: var(--sunk);
}
/* The roles are the argument. Stating them in the margin turns the answer
   from an assertion into a claim with reasons attached. */
.roles { display: flex; flex-direction: column; align-items: flex-start; gap: 5px; margin-top: 14px; }
.role {
  font-size: var(--t-xs); font-weight: var(--w-label); color: var(--dim);
  padding: 5px 11px; border-radius: var(--r-pill); background: var(--sunk);
}
/* One block per city inside a place's card. A hairline between them, so the
   card still reads as one place rather than several. */
.city {
  padding: 22px 22px 20px; margin: 0 0 22px; border-radius: var(--r-md);
  box-shadow: inset 0 0 0 1px var(--hair);
  transition: box-shadow var(--t-fast), transform var(--t-fast) var(--ease);
}
.city:hover { box-shadow: var(--e-lift); }
.city:first-of-type { margin-top: 0; }
/* ─── The reveal ──────────────────────────────────────────────────────────
   The moment the product exists for, so it is staged rather than listed:
   what you love, stated quietly; the squiggle; then the counterpart at
   display size with room around it. Vertical on every screen — the answer
   should arrive *after* the thing it answers, and side by side gives them
   equal billing. */
.reveal {
  padding: var(--s5) var(--s4) var(--s5);
  margin: 0 0 var(--s4);
  box-shadow: none;
  max-width: 640px;
}
.reveal:hover { box-shadow: none; }

.eyebrow {
  display: block; font-size: var(--t-xs); font-weight: var(--w-label);
  letter-spacing: .1em; text-transform: uppercase; color: var(--faint);
  margin-bottom: var(--s1);
}
.known { margin-bottom: var(--s3); }
.kname {
  font-size: var(--t-lg); font-weight: var(--w-label);
  color: var(--dim); letter-spacing: -0.01em; line-height: 1.2;
}
.kmeta, .hmeta { font-size: var(--t-sm); color: var(--faint); margin-top: var(--s1); }

/* The squiggle turns the corner: the mark points up-right by default, and
   here the eye needs to travel down the page. */
.squiggle { margin: var(--s2) 0 var(--s3); }
.squiggle .loop-empty {
  width: 40px; height: 40px; color: var(--accent);
  transform: rotate(58deg);
}

.hero { margin-bottom: var(--s4); }
.hero .answer {
  font-size: clamp(30px, 3.4vw, 42px); font-weight: var(--w-display);
  letter-spacing: -0.035em; line-height: 1.06; margin: var(--s1) 0 0;
  color: var(--ink); text-wrap: balance;
}
.hero .hmeta { margin-top: var(--s2); }
.hero .strength { margin: var(--s3) 0 0; }

/* The explanation is the product. It gets a reading measure and the room
   to be read, not a caption slot. */
.reveal .why.big {
  font-size: var(--t-md); line-height: 1.65; color: var(--dim);
  max-width: 56ch; margin: 0 0 var(--s4); opacity: 1;
}

/* Shared traits, as the corpus knows them. */
.traits {
  list-style: none; display: flex; flex-wrap: wrap; gap: var(--s1);
  padding: 0; margin: 0 0 var(--s4);
}
.traits li {
  font-size: var(--t-xs); color: var(--accent-deep); background: var(--mint);
  padding: 6px var(--s3); border-radius: var(--r-pill);
}

/* Entrance: the counterpart arrives a beat after the place you named, which
   is the difference between a reveal and a page that was already there. */
@keyframes rise { from { opacity: 0; transform: translateY(10px); } }
.reveal > * { animation: rise var(--t-slow) var(--ease) both; }
.reveal > .known    { animation-delay: 0ms; }
.reveal > .squiggle { animation-delay: 90ms; }
.reveal > .hero     { animation-delay: 180ms; }
.reveal > .why      { animation-delay: 280ms; }
.reveal > .traits   { animation-delay: 340ms; }
.reveal > .map, .reveal > .acts, .reveal > details, .reveal > .verify {
  animation-delay: 400ms;
}
@media (prefers-reduced-motion: reduce) {
  .reveal > * { animation: none; }
}

.cityname {
  font-size: var(--t-sm); font-weight: var(--w-label); color: var(--accent);
  margin-bottom: 6px; display: block; letter-spacing: 0; text-transform: none;
}
.answer {
  font-size: clamp(24px, 2.6vw, 32px); font-weight: var(--w-display); letter-spacing: -0.03em;
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
  border-radius: var(--r-md); overflow: hidden; background: var(--chip);
  display: block; text-decoration: none;
}
.map img {
  position: absolute; width: 256px; height: 256px; max-width: none;
  border: 0; image-rendering: auto;
}
.map .pin {
  position: absolute; left: 50%; top: 50%; width: 16px; height: 16px;
  margin: -8px 0 0 -8px; border-radius: var(--r-pill);
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
  position: absolute; right: 0; bottom: 0; font-size: var(--t-xs); line-height: 1.6;
  padding: 1px 6px; background: color-mix(in srgb, var(--panel) 82%, transparent);
  color: var(--faint); border-radius: var(--r-sm) 0 0 0;
}
.map:hover { box-shadow: var(--lift); }

/* Actions under an answer: where to go, and whether you're keeping it. */
.acts { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-top: 16px; }
.acts a, .acts .save, .acts .share {
  font-size: var(--t-sm); font-weight: var(--w-label); padding: 11px 16px;
  border-radius: var(--r-pill); background: var(--chip); color: var(--accent-deep);
  text-decoration: none; display: inline-flex; align-items: center; gap: 6px;
  transition: background var(--t-fast), color var(--t-fast), transform var(--t-fast) var(--ease);
}
.acts a:hover, .acts .save:hover, .acts .share:hover {
  background: var(--accent-deep); color: var(--on-accent);
}
/* A save is yours, not the site's — pink marks it as the one thing on the
   page you put there. */
.acts .share { background: var(--chip); color: var(--accent-deep); }
.acts .share.done { background: var(--accent); color: var(--on-accent); }
.acts .save[aria-pressed=true] { background: var(--pink-deep); color: var(--on-pink); }
.acts .save[aria-pressed=true]:hover { background: var(--pink-deep); }
.acts .out { font-size: var(--t-xs); opacity: .6; }
.savecount { font-size: var(--t-xs); color: var(--faint); font-weight: var(--w-label); margin-left: 4px; }
#savedbtn[hidden] { display: none; }

.why { color: var(--dim); font-size: var(--t-md); line-height: 1.7; margin-top: 6px; max-width: 60ch; }
.why.big { font-size: var(--t-md); line-height: 1.6; color: var(--dim); opacity: 1; max-width: 68ch; }
.cname { font-weight: var(--w-label); font-size: var(--t-md); letter-spacing: -0.01em; }
.alt { padding: 14px 0 0 18px; box-shadow: inset 2px 0 0 var(--hair); margin-top: 14px; }
details { margin-top: 18px; }
summary {
  cursor: pointer; font-size: var(--t-sm); font-weight: var(--w-label); color: var(--ink);
  text-decoration: underline; text-underline-offset: 3px;
  list-style: none; display: inline-flex; align-items: center; gap: 7px;
  transition: color var(--t-fast);
}
summary::-webkit-details-marker { display: none; }
summary::before {
  content: "+"; display: grid; place-items: center;
  width: 20px; height: 20px; border-radius: var(--r-pill);
  background: var(--chip); color: var(--accent-deep); font-size: var(--t-sm); line-height: 1;
  transition: transform var(--t-fast) var(--ease);
}
details[open] summary::before { content: "\2013"; transform: rotate(180deg); }
summary:hover { color: var(--dim); }
.link {
  font: inherit; font-size: var(--t-sm); background: none; color: var(--faint);
  cursor: pointer; text-decoration: underline; text-underline-offset: 3px;
  padding: 0; transition: color var(--t-fast);
}
.link:hover { color: var(--dim); }
/* ─── Sign-in sheet ───────────────────────────────────────────────────── */
.sheet {
  position: fixed; inset: 0; z-index: 90; display: grid; place-items: center;
  background: rgba(20,20,20,.45); padding: 24px;
}
.sheet[hidden] { display: none; }
.sheetbox {
  background: var(--panel); border-radius: var(--r-md); padding: 34px 32px 28px;
  max-width: 440px; width: 100%; box-shadow: var(--lift); position: relative;
}
.sheetbox h2 { font-size: var(--t-xl); font-weight: var(--w-display); letter-spacing: -0.03em; margin: 0 0 10px; }
.sheetbox p { color: var(--dim); font-size: var(--t-md); margin: 0 0 20px; }
.sheetx {
  position: absolute; top: 14px; right: 14px; width: 34px; height: 34px;
  background: none; color: var(--dim); font-size: var(--t-xl); line-height: 1;
  display: grid; place-items: center;
}
.sheetx:hover { background: var(--sunk); }
.sheetbox input {
  width: 100%; font-size: var(--t-md); padding: 15px 18px; border-radius: var(--r-sm);
  background: var(--panel); box-shadow: inset 0 0 0 1px var(--hair-2);
}
.next.wide { width: 100%; height: auto; border-radius: var(--r-sm); padding: 15px; font-size: var(--t-md); font-weight: var(--w-label); }
.sheetnote { font-size: var(--t-sm); margin: 14px 0 0 !important; min-height: 20px; }

/* ─── Verification, on the card ───────────────────────────────────────── */
.verify { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-top: 14px; }
.verify[hidden] { display: none; }
.vbtn {
  font-size: var(--t-sm); font-weight: var(--w-label); padding: 9px 14px; border-radius: var(--r-pill);
  background: none; color: var(--dim); box-shadow: inset 0 0 0 1px var(--hair-2);
}
.vbtn:hover { color: var(--ink); box-shadow: inset 0 0 0 1px var(--ink); }
.vbtn[aria-pressed=true] { background: var(--accent); color: var(--on-accent); box-shadow: none; }
.vbtn.no[aria-pressed=true] { background: var(--dim); color: var(--paper); }
.tally { font-size: var(--t-sm); color: var(--faint); }

.nudge {
  position: fixed; left: 50%; bottom: 30px; transform: translate(-50%, 14px);
  background: var(--ink); color: var(--paper); padding: 13px 22px;
  border-radius: var(--r-pill); font-size: var(--t-md); font-weight: var(--w-label); z-index: 80;
  opacity: 0; transition: opacity var(--t-slow), transform var(--t-slow) var(--ease);
  box-shadow: var(--lift);
}
.nudge.in { opacity: 1; transform: translate(-50%, 0); }

.empty { text-align: center; color: var(--dim); padding: 80px 20px; font-size: var(--t-md); }
.empty p { margin: 18px 0 0; }
.loop-spin { width: 54px; height: 54px; color: var(--hair-2); }
.more { text-align: center; color: var(--dim); font-size: var(--t-sm); padding: 16px 20px 48px; }

/* ─── Nav ─────────────────────────────────────────────────────────────── */
.topnav {
  position: sticky; top: 0; z-index: 300; padding: 14px 20px 10px;
  background: color-mix(in srgb, var(--paper) 88%, transparent);
  backdrop-filter: saturate(1.3) blur(10px);
  -webkit-backdrop-filter: saturate(1.3) blur(10px);
}
.navrow {
  display: flex; gap: 8px; justify-content: center; flex-wrap: wrap;
  box-shadow: inset 0 0 0 1.5px var(--ink); border-radius: var(--r-pill);
  padding: 5px; width: max-content; margin: 0 auto; background: var(--paper);
}
.navpill {
  font-size: var(--t-sm); font-weight: var(--w-label); padding: 7px 18px; border-radius: var(--r-pill);
  background: none; color: var(--ink);
  box-shadow: inset 0 0 0 1.5px var(--ink);
  text-decoration: underline; text-underline-offset: 3px;
  text-decoration-thickness: 1px;
}
.navpill:hover { background: var(--sunk); }
/* The page you're on is the one that isn't a link any more. */
.navpill[aria-current=page] { text-decoration: none; background: var(--paper); font-weight: var(--w-label); }
.navpill:not([aria-current=page]):hover { text-decoration-thickness: 2px; }

@media (max-width: 560px) {
  .navrow { gap: 5px; padding: 4px; }
  .navpill { font-size: var(--t-sm); padding: 6px 12px; }
}

/* ─── Your Elsewhere ──────────────────────────────────────────────────────
   A collection, not an account screen. The heading is the only large thing;
   everything else earns its size. Sections are separated by rhythm and one
   hairline rather than by wrapping each in a box — the page should not be a
   stack of rounded rectangles. */
.acct { max-width: 660px; }
.accth {
  display: flex; align-items: center; gap: var(--s3);
  font-size: clamp(32px, 4.4vw, 46px); letter-spacing: -0.04em; margin: 0 0 var(--s3);
}
.acctmark { display: inline-flex; }
.acctmark .loop-empty { width: 30px; height: 30px; color: var(--accent); }
/* The one place the mark moves, and only on a page you arrive at rarely. */
@media (prefers-reduced-motion: no-preference) {
  .acctmark .loop-empty { animation: nudge 4s var(--ease) infinite; }
  @keyframes nudge {
    0%, 82%, 100% { transform: rotate(0); }
    88% { transform: rotate(-12deg) scale(1.06); }
    94% { transform: rotate(6deg); }
  }
}
/* Quieter than the old lede: it is a reassurance, not an instruction. */
.acctnote {
  font-size: var(--t-sm); color: var(--faint); max-width: 46ch;
  margin: 0 0 var(--s6); line-height: 1.6;
}
.acctsec { margin: 0 0 var(--s6); }
.acctsec + .acctsec { padding-top: var(--s6); box-shadow: inset 0 1px 0 var(--hair); }
.sech {
  font-size: var(--t-xs); font-weight: var(--w-label); letter-spacing: .12em;
  text-transform: uppercase; color: var(--faint); margin: 0 0 var(--s4);
}
.sech .count { color: var(--hair-2); margin-left: var(--s1); }

/* A saved pair, as a small editorial object. Hairline top rule instead of a
   box on every side, so a list of them reads as a collection rather than as
   a dashboard. */
.savedgrid { display: grid; gap: 0; }
.pair-card {
  padding: var(--s4) var(--s3);
  box-shadow: inset 0 1px 0 var(--hair);
  transition: background var(--t-fast) var(--ease);
}
.pair-card:first-child { box-shadow: none; }
.pair-card:hover { background: var(--mint); }
.pc-line { display: flex; align-items: baseline; gap: var(--s2); flex-wrap: wrap; }
.pc-name { font-size: var(--t-lg); font-weight: var(--w-label); color: var(--dim); letter-spacing: -0.01em; }
.pc-city { font-size: var(--t-xs); color: var(--faint); text-transform: uppercase; letter-spacing: .08em; }
.pc-tag {
  font-size: var(--t-xs); color: var(--accent-deep); background: var(--mint);
  padding: 3px var(--s2); border-radius: var(--r-pill); margin-left: auto;
}
.pc-mid {
  display: flex; align-items: center; gap: var(--s2);
  margin: var(--s2) 0; color: var(--accent);
  font-size: var(--t-xs); letter-spacing: .08em; text-transform: uppercase;
}
.pc-mid .loop-empty { width: 22px; height: 22px; transform: rotate(58deg); }
.pc-hero { font-size: var(--t-xl); font-weight: var(--w-display); letter-spacing: -0.03em;
  color: var(--ink); line-height: 1.1; }
.pc-acts { display: flex; gap: var(--s3); margin-top: var(--s3); }
.pc-acts a, .pc-acts button {
  font-size: var(--t-sm); font-weight: var(--w-label); color: var(--dim);
  background: none; padding: var(--s2) 0; text-decoration: underline;
  text-underline-offset: 3px; min-height: 40px;
}
.pc-acts a:hover, .pc-acts button:hover { color: var(--accent-deep); }
/* Removing one collapses rather than vanishing, so the list keeps its place. */
.pair-card.going {
  opacity: 0; transform: translateX(-8px);
  max-height: 0; padding-block: 0; overflow: hidden;
  transition: opacity var(--t-fast), transform var(--t-fast),
              max-height var(--t-slow) var(--ease), padding-block var(--t-slow);
}

/* Taste. Chips arrive in sequence, which is most of the charm. */
.tastechips { display: flex; flex-wrap: wrap; gap: var(--s2); }
.tastechips li {
  list-style: none; font-size: var(--t-sm); color: var(--accent-deep);
  background: var(--mint); padding: var(--s2) var(--s3); border-radius: var(--r-pill);
  animation: rise var(--t-slow) var(--ease) both;
}
.tasteempty .big { font-size: var(--t-lg); font-weight: var(--w-label); color: var(--ink); margin: 0 0 var(--s2); }
.tasteempty .small { font-size: var(--t-sm); color: var(--faint); margin: 0 0 var(--s3); }
.tastecount { font-size: var(--t-xs); color: var(--accent-deep); letter-spacing: .06em; }

.onward {
  display: inline-block; font-size: var(--t-md); font-weight: var(--w-label);
  color: var(--accent-deep); text-decoration: none; padding: var(--s2) 0;
  border-bottom: 1.5px solid var(--accent-soft); transition: border-color var(--t-fast);
}
.onward:hover { border-bottom-color: var(--accent); }

@media (max-width: 520px) {
  .acct { padding-left: var(--s4); padding-right: var(--s4); }
  .pc-tag { margin-left: 0; }
  .pc-hero { font-size: var(--t-lg); }
  .pc-acts { gap: var(--s4); }
}

/* ─── Simple pages ────────────────────────────────────────────────────── */
.page { max-width: 720px; margin: 0 auto; padding: 60px 24px 120px; }
.page[hidden] { display: none; }
.page h1 { font-size: clamp(30px, 4vw, 44px); font-weight: var(--w-display); letter-spacing: -0.035em; margin: 0 0 14px; }
.page .lede { font-size: var(--t-lg); color: var(--dim); margin: 0 0 36px; line-height: 1.55; }
.page h2 { font-size: var(--t-lg); font-weight: var(--w-display); margin: 34px 0 12px; }
.page label { display: block; font-size: var(--t-sm); font-weight: var(--w-label); margin: 0 0 6px; }
.page input, .page textarea, .page select {
  width: 100%; font: inherit; font-size: var(--t-md); padding: 13px 16px; border-radius: var(--r-sm);
  background: var(--panel); color: var(--ink); box-shadow: inset 0 0 0 1px var(--hair-2);
  margin-bottom: 16px;
}
.page textarea { min-height: 120px; resize: vertical; }
.page .btn {
  font-size: var(--t-md); font-weight: var(--w-label); padding: 14px 26px; border-radius: var(--r-pill);
  background: var(--accent-deep); color: var(--on-accent);
}
.page .btn:hover { filter: brightness(1.08); }
.page .quiet { color: var(--dim); font-size: var(--t-md); }
.savedgrid { display: grid; gap: 10px; }
.savedrow {
  display: flex; align-items: baseline; gap: 10px; padding: 14px 16px;
  border-radius: var(--r-md); box-shadow: inset 0 0 0 1px var(--hair);
}
.savedrow b { font-size: var(--t-md); }
.savedrow span { color: var(--faint); font-size: var(--t-sm); }
.savedrow a { margin-left: auto; color: var(--accent-deep); font-size: var(--t-sm); font-weight: var(--w-label); }

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
/* The corners, drawn. Set in the ink colour at very low opacity rather than
   in a tint, so they read as pencil on the page instead of as coloured
   shapes — and so they hold up in dark mode without a second palette. */
.decor {
  position: absolute; pointer-events: none; color: var(--ink);
  opacity: .16; z-index: 0;
}
:root[data-theme="dark"] .decor { opacity: .2; }
/* Sat inside the frame, not hanging off it. Negative offsets on a container
   that clips its overflow cut whole fish in half at the viewport edge, which
   read as broken artwork rather than as a crop. */
.decor-fish { top: 12px; right: 12px; width: min(30vw, 380px); height: auto; }
.decor-balloons { bottom: 12px; left: 12px; width: min(21vw, 260px); height: auto; }

@media (max-width: 760px) {
  /* At phone width they crowd the sentence rather than framing it. */
  .decor-fish { width: 52vw; top: 6px; right: 6px; }
  .decor-balloons { width: 36vw; bottom: 6px; left: 6px; }
}
.setup .inner > * { position: relative; z-index: 1; }
/* The sentence outranks its siblings, because it contains the menus. Every
   child of .inner was given z-index 1 to lift it off the illustrations, and
   in that tie the locate button below won on document order — so an open
   dropdown had a button drawn straight through it. */
.setup .inner > .madlib { z-index: 5; }

.setup .pitch {
  font-size: clamp(34px, 4.6vw, 56px); font-weight: var(--w-display); letter-spacing: -0.035em;
  line-height: 1.08; margin: 0 0 20px; color: var(--ink);
}
/* Second line of the slogan — it needs its own line, or the comma runs
   straight into "It's". */
.setup .pitch em { font-style: normal; color: var(--accent); display: block; }
.setup #heroplace { color: var(--accent); }
.setup .sub {
  font-size: var(--t-lg); color: var(--dim); max-width: 46ch; margin: 0 auto 44px; line-height: 1.55;
}
.setup .sub em { color: var(--ink); font-style: normal; font-weight: var(--w-label); }

/* One sentence, not two form fields. The cities are words in it that happen
   to open a menu, and the loop between them is the verb: this, translated
   into that. Nothing here is boxed, because a box would make it a form
   again. */
.madlib {
  display: flex; align-items: center; justify-content: center; flex-wrap: wrap;
  gap: 6px 14px; margin: 0 auto 34px; max-width: 900px;
  font-size: clamp(22px, 3vw, 34px); font-weight: var(--w-label); letter-spacing: -0.02em;
  color: var(--dim);
}
.madlib .lead { color: var(--dim); }
.madlib .citypick { display: inline-flex; }
.madlib .citybtn {
  font-size: inherit; font-weight: var(--w-display); letter-spacing: -0.03em;
  color: var(--ink); background: none; box-shadow: none;
  padding: 2px 4px; gap: 8px; border-radius: var(--r-sm);
  /* Underlined the way a fillable blank is, in the accent so it reads as
     the changeable part of the sentence. */
  box-shadow: inset 0 -3px 0 var(--accent);
}
.madlib .citybtn:hover { color: var(--accent); box-shadow: inset 0 -3px 0 var(--accent); transform: none; }
.madlib .citybtn .caret { font-size: .42em; color: var(--accent); }
.loop { width: 1.5em; height: 1.5em; color: var(--accent); flex: none; }
/* The mark, doing the thing the mark means. Reversing the sentence is the
   most common second thought someone has here — "wait, I want it the other
   way" — and it deserves the logo rather than a pair of arrows. */
.swap {
  background: none; padding: 6px; border-radius: var(--r-pill); display: grid;
  place-items: center; color: var(--accent);
}
.swap:hover { background: var(--accent-soft); }
.swap.spin .loop { animation: flip .5s var(--ease); }
@keyframes flip { from { transform: rotate(0) scale(1); } 50% { transform: rotate(180deg) scale(.8); } to { transform: rotate(360deg) scale(1); } }

/* Offered, never taken. The prompt only fires if this is pressed. */
.locate {
  display: block; margin: 22px auto 0; background: none; color: var(--dim);
  font-size: var(--t-sm); font-weight: var(--w-label); padding: 8px 14px; border-radius: var(--r-pill);
  box-shadow: inset 0 0 0 1px var(--hair-2);
}
.locate:hover { color: var(--ink); box-shadow: inset 0 0 0 1px var(--ink); }
.locate[hidden] { display: none; }

/* The verb, again — the same mark, filled in and clickable. */
.next {
  width: 62px; height: 62px; padding: 0; border-radius: var(--r-pill);
  background: var(--accent); color: var(--on-accent);
  display: grid; place-items: center; box-shadow: none; flex: none;
}
.next:hover { background: var(--accent-deep); }
.next:active { opacity: .85; }
.go-arrow { width: 26px; height: 26px; }

/* ─── First visit: choose a city ──────────────────────────────────────── */
.pick { padding: 76px 24px 40px; text-align: center; }
.pick[hidden] { display: none; }
.pick .wrap { max-width: 700px; margin: 0 auto; }
.pick .pick .brand { font-family: var(--display); font-size: var(--t-md); color: var(--dim); margin-bottom: 26px; }
.sub { font-size: var(--t-md); color: var(--dim); max-width: 30em; margin: 0 auto 44px; line-height: 1.55; }
.pick .q { font-size: var(--t-md); font-weight: var(--w-label); color: var(--dim); margin: 0 0 16px; }
.cities { display: flex; gap: 12px; justify-content: center; flex-wrap: wrap; }
.cities button {
  font-size: var(--t-lg); font-weight: var(--w-display); letter-spacing: -0.02em;
  padding: 20px 34px; border-radius: var(--r-md);
  background: var(--panel); color: var(--ink); box-shadow: var(--shadow);
}
.cities button:hover { box-shadow: var(--e-lift); color: var(--accent-deep); }
.cities button:active { background: var(--sunk); }

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
  button.brand { font-size: var(--t-lg); padding-right: 8px; }
  .bar { gap: 10px; }
  .from { display: none; }        /* "I know" is implied by the city control */
  main { padding: 8px 16px 70px; }
  .crumb { padding: 16px 18px 2px; }
  .card { grid-template-columns: 1fr; gap: 18px; padding: 24px 0 34px; }
  .card > .head { position: static; }
  .answer { font-size: var(--t-xl); }
  /* Three lines on a phone. Left to wrap on its own, "an H-E-B," lands
     alone on line two as a stranded fragment; breaking deliberately puts the
     place name at the head of its own line where it reads as the subject. */
  /* The pill is a row of segments, which needs width it doesn't have here.
     Stack it: each question gets a full-width row, the divider becomes a
     rule between them, and the verb spans the bottom. */
  .pair {
    flex-direction: column; align-items: stretch; border-radius: var(--r-md);
    padding: 8px; gap: 2px; max-width: 420px;
  }
  .leg { padding: 14px 18px; border-radius: var(--r-md); }
  .leg + .leg { box-shadow: inset 0 1px 0 var(--hair); }
  .leg .citybtn { font-size: var(--t-md); }
  .next { width: 54px; height: 54px; padding: 0; }
  /* Phones: start at the top, do not centre.
     `justify-content: center` inside a 100vh section is right on a desktop,
     where the composition has room to breathe. On a phone the content is
     barely shorter than the viewport, so centring buys nothing and pushes
     the headline into the middle of the screen with a dead band above it —
     which is what the top of the page looked like. */
  .setup {
    /* Still fills the screen, but packs to the top. Dropping the height
       entirely moved the bottom-anchored balloons up into the controls;
       keeping it puts the empty space back at the bottom, which is where
       a margin drawing belongs. dvh so mobile browser chrome doesn't
       leave a gap under the fold. */
    min-height: 100dvh; justify-content: flex-start;
    padding: var(--s4) var(--s4) var(--s6);
  }
  .setup .pitch { margin-bottom: var(--s3); }
  .setup .sub { font-size: var(--t-md); margin-bottom: var(--s5); }
  .locate { margin-top: var(--s4); }

  /* The fish go. They live in the top-right corner, which on a phone is the
     same corner as the headline — there is no position for them that is both
     visible and out of the way, so rather than shrink them into a smudge
     they sit this size out. The balloons stay: bottom-left is genuinely
     empty space on a phone, and they are small enough to read as a margin
     drawing rather than as content. */
  .decor-fish { display: none; }
  .decor-balloons { width: 30vw; max-width: 150px; bottom: 8px; left: 8px; opacity: .12; }
  .setup .pitch .hl2 { display: block; }
  .setup .pitch .l1 { white-space: normal; }
  .pitch { margin-bottom: 24px; font-size: clamp(28px, 8.4vw, 40px); }
  .sub { font-size: var(--t-md); margin-bottom: 32px; }
  .cities button { font-size: var(--t-md); padding: 16px 24px; flex: 1 1 40%; }
  .stage {
    padding: var(--s4) 18px 140px; min-height: 100dvh; justify-content: flex-start;
  }
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
<!-- Outlined pills, in a row, on both screens — step one had no header at
     all until now. Deliberately unstyled-looking: an outline and an
     underline, no fill, so it reads as a set of links rather than a toolbar. -->
<nav class="topnav">
  <div class="navrow">
    <button class="navpill" data-page="home">home</button>
    <button class="navpill" data-page="account">account</button>
    <button class="navpill" data-page="suggestions">suggestions</button>
  </div>
</nav>

<!-- Step one: where you're coming from and where you're going. On one
     screen with the search box, this was three simultaneous decisions and
     no indication which to make first. Split out, each screen asks one
     question. The city pickers live here and nowhere else. -->
<section id="setup" class="setup" hidden>
  __DECOR__
  <div class="inner">
    <h1 class="pitch"><span class="l1" id="line1">Every city has<span class="hl2"> <span id="heroart">an</span> <span id="heroplace">H-E-B</span>,</span></span><em>It\'s just elsewhere.</em></h1>
    <p class="sub">Tell us the city you know and the one you\'re headed to.
    We\'ll translate your favorite places.</p>

    <!-- A div, not a p: each city menu is a div, and a div inside a
         paragraph implicitly closes it — the parser was throwing away
         everything after the first city. -->
    <div class="madlib">
      <span class="lead">I know</span>
      <span class="citypick">
        <select id="srcsel" class="native" title="Which city you know" tabindex="-1"></select>
        <button class="citybtn" id="citybtn" aria-haspopup="listbox" aria-expanded="false"></button>
        <div class="citymenu" id="citymenu" role="listbox" hidden></div>
      </span>
      <!-- The loop belongs between the two cities. It is the sentence's verb:
           not "go northeast" but "translate this into that". -->
      <button class="swap" id="swapbtn" title="Swap the two cities"
              aria-label="Swap the two cities">__LOOP_INLINE__</button>
      <span class="lead">show me</span>
      <span class="citypick">
        <select id="dstsel" class="native" title="Which city you\'re going to" tabindex="-1"></select>
        <button class="citybtn" id="dstbtn" aria-haspopup="listbox" aria-expanded="false"></button>
        <div class="citymenu" id="dstmenu" role="listbox" hidden></div>
      </span>
      <button class="next" id="nextbtn" aria-label="Show me">__LOOP_NEXT__</button>
    </div>
    <button class="locate" id="locatebtn">Use my location</button>
  </div>
</section>

<div id="app" hidden>
  <header id="bar"><div class="bar">
    <button class="brand" id="homebtn" title="Start over">
      <img src="/favicon.svg?v=2" alt="" width="30" height="30">Elsewhere</button>
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
    __DECOR__
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


<!-- Account. Saves already work without an account, so this shows them
     whether or not anyone is signed in, and is honest that syncing is the
     part still waiting on a backend. -->
<section class="page acct" id="page-account" hidden>
  <h1 class="accth">Your Elsewhere <span class="acctmark">__LOOP_EMPTY__</span></h1>
  <p class="acctnote" id="acctlede"></p>
  <div id="acctauth"></div>

  <section class="acctsec">
    <h2 class="sech">Saved places <span class="count" id="acctcount"></span></h2>
    <div class="savedgrid" id="acctsaved"></div>
  </section>

  <section class="acctsec taste">
    <h2 class="sech">Your taste, according to Elsewhere</h2>
    <div id="accttaste"></div>
  </section>

  <a class="onward" id="acctonward" href="#">Find another elsewhere &rarr;</a>
</section>

<!-- Suggestions. The corpus is hand-curated, so the most useful thing anyone
     can send is a place we missed or a city worth adding. -->
<section class="page" id="page-suggestions" hidden>
  <h1>Suggestions</h1>
  <p class="lede">Every place in here was chosen by hand, which means the gaps
    were chosen by hand too. Tell us what\'s missing — a place a local would
    name, or a city worth adding.</p>
  <form id="sugform">
    <label for="sugcity">City</label>
    <input id="sugcity" placeholder="Austin, or somewhere we don\'t cover yet" required>
    <label for="sugplace">Place</label>
    <input id="sugplace" placeholder="What should be in here?" required>
    <label for="sugwhy">Why it matters</label>
    <textarea id="sugwhy" placeholder="What role does it play that nothing else does? This is the part that makes a good match possible."></textarea>
    <button class="btn" type="submit">Send it</button>
    <p class="quiet" id="sugnote" style="margin-top:14px"></p>
  </form>
</section>

<!-- Sign-in. A single field and no password: nothing to store, nothing to
     leak, nothing to reset. Hidden entirely when the accounts backend
     isn't configured. -->
<div class="sheet" id="signin" hidden>
  <div class="sheetbox">
    <button class="sheetx" id="signinx" aria-label="Close">×</button>
    <h2>Verify a match</h2>
    <p>Locals settle this better than a model does. Sign in with your email —
       we\'ll send a link, there\'s no password.</p>
    <form id="signinform">
      <input type="email" id="email" placeholder="you@example.com" required
             autocomplete="email" spellcheck="false">
      <button class="next wide" type="submit">Send me a link</button>
    </form>
    <p class="sheetnote" id="signinnote"></p>
  </div>
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

/* Slugs are the storage format, not the display one: "los_angeles" has to
   reach the page as "Los Angeles". */
const title = s => String(s).split(/[_-]/)
  .map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(" ");
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

/* One drawing per category, in the same hand as the fish: stroke only,
   currentColor, nothing that needs a second colour or a fill. Each is a
   single recognisable object rather than a symbol — a pint, an awning, a
   row of roofs — because at this size an abstraction just reads as a blob. */
const ICONS = {
  Food:
    '<path d="M12 8v10a3 3 0 0 0 3 3v11"/><path d="M9 8v6M15 8v6"/>' +
    '<path d="M28 8c-3 2-4 6-4 9s1 4 3 4h1v11"/>',
  Drinks:
    '<path d="M13 12h14l-1.6 18a2 2 0 0 1-2 1.8h-6.8a2 2 0 0 1-2-1.8Z"/>' +
    '<path d="M13.4 17.5h13.2"/><path d="M27 15h3.5a2.5 2.5 0 0 1 0 5H26.6"/>' +
    '<path d="M16 8.5c1.4-1.6 3-1.6 4.4 0M22 8.5c1.2-1.4 2.6-1.4 3.8 0"/>',
  Coffee:
    '<path d="M10 15h16v9a7 7 0 0 1-7 7h-2a7 7 0 0 1-7-7Z"/>' +
    '<path d="M26 17.5h3a3.5 3.5 0 0 1 0 7h-3"/><path d="M8 34h20"/>' +
    '<path d="M15 7c-1.2 1.8-1.2 3.2 0 5M21 6.5c-1.2 1.8-1.2 3.2 0 5"/>',
  Groceries:
    '<path d="M10 14h20l-1.5 18a2 2 0 0 1-2 1.9H13.5a2 2 0 0 1-2-1.9Z"/>' +
    '<path d="M16 14V9.5a4 4 0 0 1 8 0V14"/>' +
    '<path d="M20 20c-3.4 1.6-4.6 5-3 8 3.2 1 5.8-1 6-4.5"/><path d="M20 28v-6"/>',
  Shops:
    '<path d="M9 17h22v15H9Z"/><path d="M7 17l3-7h20l3 7"/>' +
    '<path d="M9 17c0 2.4 1.6 4 3.7 4s3.6-1.6 3.6-4M16.3 17c0 2.4 1.6 4 3.7 4s3.7-1.6 3.7-4' +
    'M23.7 17c0 2.4 1.5 4 3.6 4s3.7-1.6 3.7-4"/>' +
    '<path d="M17 32v-7h6v7"/>',
  Outdoors:
    '<path d="M4 30l9-14 5.5 8"/><path d="M14 30l8.5-13L34 30Z"/>' +
    '<path d="M4 30h32"/><circle cx="10" cy="10" r="3.4"/>',
  Culture:
    '<path d="M8 32h24"/><path d="M10 32V16M16 32V16M24 32V16M30 32V16"/>' +
    '<path d="M7 16h26"/><path d="M20 5l14 9H6Z"/>',
  Neighborhoods:
    '<path d="M4 32h32"/><path d="M6 32V20l7-6 7 6v12"/><path d="M20 32V23l6-5 6 5v9"/>' +
    '<path d="M11 32v-6h4v6"/><path d="M24 27h4"/>',
  Fitness:
    '<path d="M6 20h4M30 20h4"/><path d="M10 14h4v12h-4ZM26 14h4v12h-4Z"/>' +
    '<path d="M14 20h12"/>',
};

const iconFor = key =>
  ICONS[key]
    ? `<svg class="cicon" viewBox="0 0 40 40" fill="none" stroke="currentColor"
        stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"
        aria-hidden="true">${ICONS[key]}</svg>`
    : "";

function renderCats() {
  const counts = {};
  for (const m of S.matches) {
    if (!m.cities[dest]) continue;
    counts[groupOf(m)] = (counts[groupOf(m)] || 0) + 1;
  }
  document.getElementById("cats").innerHTML = GROUPS
    .filter(([key]) => counts[key])
    .map(([key, label]) =>
      `<button data-cat="${key}">${iconFor(key)}<span class="clabel">${label}</span>
         <span class="n">${counts[key]} place${counts[key] === 1 ? "" : "s"}</span></button>`)
    .join("");
}

/* ── Accounts and verification ─────────────────────────────────────────
   Everything here is optional. With no backend configured the whole feature
   is absent and the rest of the site behaves exactly as it did — which is
   also the state of every deploy until the keys are set.

   The Supabase client is loaded lazily, on the first action that needs it,
   so a visitor who never verifies anything never pays for the library. */
const SB_CONFIG = __SUPABASE__;
const ACCOUNTS = !!SB_CONFIG.url;
let sb = null, me = null, myVerdicts = new Map(), tallies = new Map();

const vkey = (city, from, to, cand) => [city, from, to, cand].join("\u0000");

async function client() {
  if (sb) return sb;
  const { createClient } = await import(
    "https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2/+esm");
  sb = createClient(SB_CONFIG.url, SB_CONFIG.key);
  return sb;
}

async function initAccounts() {
  if (!ACCOUNTS) return;
  const c = await client();
  const { data } = await c.auth.getSession();
  me = data.session ? data.session.user : null;
  c.auth.onAuthStateChange((_e, session) => {
    me = session ? session.user : null;
    loadMyVerdicts().then(render);
  });
  await loadMyVerdicts();
}

async function loadMyVerdicts() {
  myVerdicts = new Map();
  if (!me) return;
  const c = await client();
  const { data } = await c.from("judgments")
    .select("source_city,source_name,target_city,candidate,verdict")
    .eq("user_id", me.id);
  for (const r of data || []) {
    myVerdicts.set(vkey(r.source_city, r.source_name, r.target_city, r.candidate), r.verdict);
  }
}

/* Counts for what's on screen, in one request rather than one per card. */
async function loadTallies(names) {
  if (!ACCOUNTS || !names.length) return;
  const c = await client();
  const { data } = await c.from("judgment_tallies")
    .select("source_city,source_name,target_city,candidate,yes_count,no_count")
    .eq("source_city", S.source).eq("target_city", dest)
    .in("source_name", names.slice(0, 60));
  for (const r of data || []) {
    tallies.set(vkey(r.source_city, r.source_name, r.target_city, r.candidate),
                { yes: r.yes_count, no: r.no_count });
  }
  paintTallies();
}

function paintTallies() {
  document.querySelectorAll(".verify").forEach(el => {
    const k = vkey(el.dataset.city, el.dataset.from, el.dataset.to, el.dataset.cand);
    const t = tallies.get(k), mine = myVerdicts.get(k);
    el.querySelectorAll(".vbtn").forEach(b =>
      b.setAttribute("aria-pressed", b.dataset.verdict === mine));
    const out = el.querySelector(".tally");
    if (!out) return;
    if (!t || (!t.yes && !t.no)) { out.textContent = ""; return; }
    // "3 say yes · 1 says no" — both sides, always. A split is information.
    const bits = [];
    if (t.yes) bits.push(`${t.yes} say${t.yes === 1 ? "s" : ""} yes`);
    if (t.no) bits.push(`${t.no} say${t.no === 1 ? "s" : ""} no`);
    out.textContent = bits.join(" \u00b7 ");
  });
}

function verifyBlock(place, city, from) {
  if (!ACCOUNTS) return "";
  return `<div class="verify" data-city="${esc(S.source)}" data-from="${esc(from || "")}"
       data-to="${esc(city)}" data-cand="${esc(place.name)}">
    <span class="tally"></span>
    <button class="vbtn" data-verdict="yes" aria-pressed="false">Locals agree</button>
    <button class="vbtn no" data-verdict="no" aria-pressed="false">Not really</button>
  </div>`;
}

async function castVerdict(el, verdict) {
  if (!me) { openSignin(); return; }
  const d = el.parentElement.dataset;
  const k = vkey(d.city, d.from, d.to, d.cand);
  const c = await client();
  const had = myVerdicts.get(k);

  if (had === verdict) {                       // pressing again withdraws it
    await c.from("judgments").delete()
      .match({ user_id: me.id, source_city: d.city, source_name: d.from,
               target_city: d.to, candidate: d.cand });
    myVerdicts.delete(k);
    bumpTally(k, verdict, -1);
  } else {
    await c.from("judgments").upsert({
      user_id: me.id, source_city: d.city, source_name: d.from,
      target_city: d.to, candidate: d.cand, verdict,
    }, { onConflict: "user_id,source_city,source_name,target_city,candidate" });
    myVerdicts.set(k, verdict);
    bumpTally(k, verdict, 1);
    if (had) bumpTally(k, had, -1);
  }
  paintTallies();
}

/* Optimistic: the count moves before the round trip returns, because waiting
   on a network call to acknowledge a click is what makes a page feel dead. */
function bumpTally(k, verdict, by) {
  const t = tallies.get(k) || { yes: 0, no: 0 };
  t[verdict === "yes" ? "yes" : "no"] = Math.max(0, t[verdict === "yes" ? "yes" : "no"] + by);
  tallies.set(k, t);
}

function openSignin() { document.getElementById("signin").hidden = false; }

if (ACCOUNTS) {
  document.getElementById("signinx").addEventListener("click", () =>
    document.getElementById("signin").hidden = true);

  document.getElementById("signinform").addEventListener("submit", async e => {
    e.preventDefault();
    const note = document.getElementById("signinnote");
    const email = document.getElementById("email").value.trim();
    note.textContent = "Sending\u2026";
    const c = await client();
    const { error } = await c.auth.signInWithOtp({
      email, options: { emailRedirectTo: location.href },
    });
    note.textContent = error
      ? `Couldn't send it: ${error.message}`
      : "Check your email — the link signs you straight in.";
  });

  document.getElementById("list").addEventListener("click", e => {
    const b = e.target.closest(".vbtn");
    if (b) castVerdict(b, b.dataset.verdict);
  });
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

/* `fromCity` and `cat` are new. A save used to record the counterpart, its
   city, and the name of the place you knew — but not which city that place
   was in, and not its category. Both were then looked up from whichever
   corpus happened to be loaded, so switching cities silently emptied the
   taste profile and the card could not say where you were coming from.
   Recording them at save time fixes both. Older saves simply lack the
   fields and degrade to what they always showed. */
function toggleSave(city, name, from, links) {
  const k = savedKey(city, name);
  if (saved.has(k)) saved.delete(k);
  else {
    const m = S && S.matches.find(x => x.name === from);
    saved.set(k, {
      city, name, from, links, at: Date.now(),
      fromCity: S ? S.source : "",
      cat: m ? groupOf(m) : "",
    });
  }
  localStorage.setItem(SAVED_KEY, JSON.stringify([...saved]));
  renderSavedBtn();
  maybeNudge();
}
const isSaved = (city, name) => saved.has(savedKey(city, name));

/* A quiet acknowledgement at three, and then never again. Enough to suggest
   the saves are adding up to something without pretending there's a profile
   behind them yet. */
function maybeNudge() {
  if (saved.size !== 3 || localStorage.getItem("elsewhere.nudged")) return;
  localStorage.setItem("elsewhere.nudged", "1");
  const el = document.createElement("div");
  el.className = "nudge";
  el.textContent = "We're getting your vibe.";
  document.body.appendChild(el);
  requestAnimationFrame(() => el.classList.add("in"));
  setTimeout(() => { el.classList.remove("in"); setTimeout(() => el.remove(), 400); }, 3200);
}

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
  if (page !== "home") p.set("page", page);
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
    page: p.get("page") || "home",
    city: p.get("city") || "",
    dest: p.get("to") || "",
    q: p.get("q") || "",
    cat: p.get("saved") ? "__saved" : p.get("browse") ? "__browse" : (p.get("in") || ""),
  };
}

addEventListener("popstate", () => {
  const u = readURL();
  if (u.page !== page) showPage(u.page, false);
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

/* What gets pasted into a text message.

   Three lines and no more: the claim, where the place is, and where the
   claim came from. A share that arrives as a paragraph doesn't get read, and
   one that arrives as a bare URL doesn't get opened — the sentence has to
   carry the idea on its own, because that's all most people will see in a
   notification.

   The link points at this exact answer, so the friend lands on the result
   rather than on the front door. */
function shareText(b) {
  const from = b.dataset.from, name = b.dataset.name;
  const here = title(S.source), there = title(b.dataset.city);
  const url = new URL(location.pathname, location.href);
  url.searchParams.set("city", S.source);
  url.searchParams.set("to", b.dataset.city);
  if (from) url.searchParams.set("q", from);

  const line = from
    ? `${here}'s ${from} is ${there}'s ${name}.`
    : `In ${there}, go to ${name}.`;
  return [line, b.dataset.map, url.toString()].filter(Boolean).join("\\n");
}

/* Native share sheet where there is one — that's the whole point on a phone,
   where "different mediums" means Messages, WhatsApp, wherever. Clipboard is
   the desktop fallback, and execCommand covers the browsers and insecure
   origins where the async clipboard API isn't available. */
async function shareAnswer(b) {
  const text = shareText(b);
  const flash = () => {
    const was = b.textContent;
    b.textContent = "Copied";
    b.classList.add("done");
    setTimeout(() => { b.textContent = was; b.classList.remove("done"); }, 1800);
  };

  if (navigator.share) {
    try { await navigator.share({ text }); return; }
    catch (e) { if (e.name === "AbortError") return; }   // they closed the sheet
  }
  try {
    await navigator.clipboard.writeText(text);
    flash();
  } catch {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.cssText = "position:fixed;opacity:0";
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand("copy"); flash(); } finally { ta.remove(); }
  }
}

/* "restaurant_tacos" is how the corpus stores it; "Tacos" is what a person
   reads. The head word is dropped when there's a more specific tail, since
   "Restaurant · Tacos" says the same thing twice. */
function prettyCat(cat) {
  const parts = String(cat).split("_");
  const word = parts.length > 1 ? parts.slice(1).join(" ") : parts[0];
  return word.charAt(0).toUpperCase() + word.slice(1);
}

/* How sure the model was, said in words.

   The corpus carries a 0-1 confidence per candidate, and it is tempting to
   print it as "92% match". It isn't one. It's the model's own rating of its
   own answer, not a measured agreement rate — the eval harness refuses to
   publish a percentage until there are thirty independent human judgments,
   and printing a precise-looking number here would undo exactly that
   discipline one card at a time. Buckets say the same thing without
   claiming arithmetic nobody did. */
function strength(c) {
  const v = typeof c.confidence === "number" ? c.confidence : null;
  if (v === null) return "";
  const [label, cls] = v >= 0.85 ? ["Dead on", "hi"]
    : v >= 0.7 ? ["Strong match", "hi"]
    : v >= 0.5 ? ["Close enough", "mid"]
    : ["Loose match", "lo"];
  return `<div class="strength ${cls}" title="Model confidence ${
    Math.round(v * 100)}% — self-rated, not measured">${label}</div>`;
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
    <button class="share" data-name="${esc(place.name)}" data-city="${esc(city)}"
      data-map="${esc(l.map || "")}" data-from="${esc(from || "")}">Share</button>
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
      const roles = (m.roles || []).slice(0, 3)
        .map(r => esc(r.replace(/_/g, " "))).join(" · ");
      const traits = (m.roles || []).slice(0, 5)
        .map(r => `<li>${esc(r.replace(/_/g, " "))}</li>`).join("");
      return `<div class="city reveal">
        <div class="known">
          <span class="eyebrow">You love</span>
          <div class="kname">${esc(m.name)}</div>
          <div class="kmeta">${esc(title(S.source))}${
            m.category ? ` \u00b7 ${esc(prettyCat(m.category))}` : ""}</div>
        </div>

        <div class="squiggle">__LOOP_EMPTY__</div>

        <div class="hero">
          <span class="eyebrow">Its counterpart in ${esc(title(t))}</span>
          <div class="answer"><span>${esc(top.name)}</span></div>
          <div class="hmeta">${esc(title(t))}${
            m.category ? ` \u00b7 ${esc(prettyCat(m.category))}` : ""}</div>
          ${strength(top)}
        </div>

        <p class="why big">${esc(top.reasoning)}</p>
        ${traits ? `<ul class="traits">${traits}</ul>` : ""}
        ${acts(top, t, m.name)}
        ${verifyBlock(top, t, m.name)}
        ${rest.length ? `<details><summary>${rest.length} other option${
          rest.length > 1 ? "s" : ""}</summary>${rest.map(x =>
          `<div class="alt"><span class="cname">${esc(x.name)}</span>
             <div class="why">${esc(x.reasoning)}</div></div>`).join("")}</details>` : ""}
      </div>`;
    }).join("");

    return `<div class="card">${blocks}</div>`;
  }).join("") + (total > rows.length
    ? `<p class="more">${total - rows.length} more match \u201c${esc(q || cat)}\u201d — keep typing to narrow it down.</p>`
    : "");
  renderSavedBtn();
  if (ACCOUNTS) { paintTallies(); loadTallies(rows.map(m => m.name)); }
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
  // Below the breakpoint the claim is allowed to wrap onto three lines, so
  // there is nothing to shrink and measuring a wrapped line would scale the
  // type down for no reason.
  if (matchMedia("(max-width: 640px)").matches) {
    line.style.setProperty("--fit", 1);
    return;
  }
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
  if (ACCOUNTS && !sb) initAccounts();
}

/* ── Where you are ─────────────────────────────────────────────────────
   Two guesses, in order of how much they cost the visitor.

   First the time zone: free, instant, requires no permission and no network
   call, and never leaves the page. "America/Chicago" narrows six cities to
   two without asking anyone anything. It cannot tell Austin from Chicago —
   they share a zone — so it picks one and lets the visitor correct it, which
   is a better opening position than defaulting to Austin for a Tokyo
   visitor.

   Then, only if the visitor presses the button, the Geolocation API: exact,
   but it costs a browser permission prompt, and firing that unbidden on page
   load is the kind of thing that makes people close a tab. The coordinates
   are compared against city centres in the browser and discarded; nothing is
   sent anywhere.

   Deliberately not used: IP geolocation. It would need a third-party lookup
   on every page load — a dependency, a cost, and a request carrying the
   visitor's address to someone else — to do a job the time zone already does
   well enough. */
const ZONE_GUESS = {
  "America/Chicago": "austin",
  "America/New_York": "chapel_hill",
  "America/Detroit": "chapel_hill",
  "America/Toronto": "chapel_hill",
  "America/Los_Angeles": "portland",
  "America/Vancouver": "portland",
  "America/Denver": "austin",
  "America/Phoenix": "los_angeles",
  "Asia/Tokyo": "tokyo",
  "Asia/Seoul": "tokyo",
  "Asia/Osaka": "tokyo",
};

function guessFromZone(known) {
  try {
    const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    const guess = ZONE_GUESS[zone];
    if (guess && known.includes(guess)) return guess;
    // Anywhere in the Americas is closer to a US city than to Tokyo.
    if (zone && zone.startsWith("America/")) {
      return known.find(c => c !== "tokyo") || known[0];
    }
    if (zone && (zone.startsWith("Asia/") || zone.startsWith("Australia/"))) {
      return known.includes("tokyo") ? "tokyo" : known[0];
    }
  } catch { /* Intl is missing on nothing we support, but it's free to guard */ }
  return "";
}

/* Great-circle distance. The cities are thousands of kilometres apart, so
   this only has to rank them, not measure them. */
function haversine(a, b, c, d) {
  const R = 6371, rad = x => (x * Math.PI) / 180;
  const dLat = rad(c - a), dLon = rad(d - b);
  const h = Math.sin(dLat / 2) ** 2 +
    Math.cos(rad(a)) * Math.cos(rad(c)) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(h));
}

async function locateMe(btn) {
  if (!navigator.geolocation) { btn.hidden = true; return; }
  const was = btn.textContent;
  btn.textContent = "Finding you…";
  try {
    const pos = await new Promise((ok, no) =>
      navigator.geolocation.getCurrentPosition(ok, no, { timeout: 8000, maximumAge: 600000 }));
    const geo = await (await fetch("/api/geo")).json();
    let best = null, bestKm = Infinity;
    for (const [city, p] of Object.entries(geo)) {
      const km = haversine(pos.coords.latitude, pos.coords.longitude, p.lat, p.lon);
      if (km < bestKm) { bestKm = km; best = city; }
    }
    if (best && best !== home) {
      home = best;
      document.getElementById("srcsel").value = home;
      drawSrc();
      dest = "";
      await fillDestinations();
    }
    btn.textContent = `Nearest: ${title(best)}`;
    setTimeout(() => { btn.textContent = was; }, 2600);
  } catch {
    // Denied, unavailable, or timed out — all the same to us. The picker
    // still works; stop offering something that didn't help.
    btn.textContent = "Couldn't get your location";
    setTimeout(() => { btn.textContent = was; }, 2600);
  }
}

/* ── Pages ─────────────────────────────────────────────────────────────
   Three of them, switched by URL so each is linkable and the back button
   works. "home" is the whole existing app; the other two are static enough
   to render on demand. */
const PAGES = ["home", "account", "suggestions"];
let page = "home";

function showPage(name, push = true) {
  page = PAGES.includes(name) ? name : "home";
  document.getElementById("page-account").hidden = page !== "account";
  document.getElementById("page-suggestions").hidden = page !== "suggestions";
  const homeOn = page === "home";
  document.getElementById("setup").hidden = !homeOn || !onSetup;
  document.getElementById("app").hidden = !homeOn || onSetup;
  document.querySelectorAll(".navpill").forEach(b =>
    b.toggleAttribute("aria-current", b.dataset.page === page));
  document.querySelectorAll(".navpill").forEach(b => {
    if (b.dataset.page === page) b.setAttribute("aria-current", "page");
    else b.removeAttribute("aria-current");
  });
  if (page === "account") renderAccount();
  if (push) syncURL();
  if (!homeOn) stopDemo();
  scrollTo({ top: 0 });
}

/* Observations, not analytics. Each phrase is tied to a category the saves
   actually fall into — nothing is inferred beyond "you keep saving these",
   which is the only claim the data supports. The wording is the part that
   makes it feel like someone noticed rather than something scored you. */
const TASTE = {
  Food: "restaurants with a point of view",
  Drinks: "bars that have been there a while",
  Coffee: "good coffee, obviously",
  Groceries: "excellent grocery stores, weirdly",
  Shops: "independent everything",
  Outdoors: "somewhere to walk it off",
  Culture: "places with a little history",
  Neighborhoods: "neighbourhood wandering",
  Fitness: "earning it first",
};

function tasteChips(rows) {
  const counts = {};
  for (const r of rows) {
    // Prefer the category recorded at save time; fall back to the loaded
    // corpus for saves made before that was stored.
    const cat = r.cat || (S && (S.matches.find(x => x.name === r.from) || {}) && groupOf(
      S.matches.find(x => x.name === r.from) || { category: "" }));
    if (cat && TASTE[cat]) counts[cat] = (counts[cat] || 0) + 1;
  }
  const picked = Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 5);
  // Only said when the saves are actually spread around.
  if (picked.length >= 3 && rows.length >= 5) picked.push(["_range", 0]);
  return picked.map(([k]) => k === "_range" ? "hard to pin down" : TASTE[k]);
}

function renderAccount() {
  const rows = [...saved.values()].sort((a, b) => b.at - a.at);

  document.getElementById("acctlede").textContent = ACCOUNTS && me
    ? `Signed in as ${me.email}. Your saves follow you.`
    : "Your saves live right here on this device. No account, no inbox clutter, no funny business.";

  const auth = document.getElementById("acctauth");
  auth.innerHTML = !ACCOUNTS ? ""
    : me ? `<button class="btn" id="signoutbtn">Sign out</button>`
         : `<button class="btn" id="signinopen">Sign in with email</button>`;

  document.getElementById("acctcount").textContent = rows.length ? rows.length : "";

  document.getElementById("acctsaved").innerHTML = rows.length
    ? rows.map(r => `<article class="pair-card" data-city="${esc(r.city)}" data-name="${esc(r.name)}">
        <div class="pc-line">
          <span class="pc-name">${esc(r.from || "Somewhere you liked")}</span>
          ${r.fromCity ? `<span class="pc-city">${esc(title(r.fromCity))}</span>` : ""}
          ${r.cat ? `<span class="pc-tag">${esc(r.cat.toLowerCase())}</span>` : ""}
        </div>
        <div class="pc-mid">__LOOP_EMPTY__ elsewhere</div>
        <div class="pc-line">
          <span class="pc-hero">${esc(r.name)}</span>
          <span class="pc-city">${esc(title(r.city))}</span>
        </div>
        <div class="pc-acts">
          ${r.links && r.links.map
            ? `<a href="${esc(r.links.map)}" target="_blank" rel="noopener noreferrer">Map</a>` : ""}
          <button class="pc-remove" type="button">Remove</button>
        </div>
      </article>`).join("")
    : `<p class="quiet">Nothing saved yet. Star a counterpart and it turns up here.</p>`;

  const taste = document.getElementById("accttaste");
  const chips = tasteChips(rows);
  if (rows.length < 3 || !chips.length) {
    const left = Math.max(0, 3 - rows.length);
    taste.innerHTML = `<div class="tasteempty">
      <p class="big">We have theories. We need more evidence.</p>
      <p class="small">Save a few more places and we'll start figuring out your type.</p>
      <p class="tastecount">${rows.length} of 3 saves before we start making assumptions</p>
    </div>`;
    if (!left) taste.querySelector(".tastecount").textContent = "Almost — one more should do it";
  } else {
    taste.innerHTML = `<ul class="tastechips">${chips.map((c, i) =>
      `<li style="animation-delay:${i * 70}ms">${esc(c)}</li>`).join("")}</ul>`;
  }
}

/* Removing collapses the card first, so the list settles instead of jumping. */
document.getElementById("page-account").addEventListener("click", e => {
  const rm = e.target.closest(".pc-remove");
  if (!rm) return;
  const card = rm.closest(".pair-card");
  const done = () => { toggleSave(card.dataset.city, card.dataset.name); renderAccount(); };
  if (matchMedia("(prefers-reduced-motion: reduce)").matches) { done(); return; }
  card.style.maxHeight = card.offsetHeight + "px";
  requestAnimationFrame(() => card.classList.add("going"));
  setTimeout(done, 320);
});

document.getElementById("acctonward").addEventListener("click", e => {
  e.preventDefault();
  showPage("home");
});


document.querySelector(".topnav").addEventListener("click", e => {
  const b = e.target.closest(".navpill");
  if (!b) return;
  showPage(b.dataset.page);
  /* "home" has to mean start over, not just "the home page is now visible".
     Clicking it from a results screen used to do nothing at all — you were
     already on the home page, so switching to it changed no state and the
     results stayed exactly where they were. It now clears the query the way
     the wordmark always has, so the two agree and the URL drops back to the
     city pair. The pair itself survives: it is the session, not the search. */
  if (b.dataset.page === "home") clearView();
});

document.getElementById("page-account").addEventListener("click", async e => {
  if (e.target.id === "signinopen") openSignin();
  if (e.target.id === "signoutbtn") { (await client()).auth.signOut(); }
});

document.getElementById("sugform").addEventListener("submit", async e => {
  e.preventDefault();
  const note = document.getElementById("sugnote");
  const body = {
    city: document.getElementById("sugcity").value.trim(),
    place: document.getElementById("sugplace").value.trim(),
    why: document.getElementById("sugwhy").value.trim(),
  };
  if (ACCOUNTS) {
    const c = await client();
    const { error } = await c.from("suggestions").insert(body);
    note.textContent = error ? `Couldn't send that: ${error.message}` : "Sent — thank you.";
    if (!error) e.target.reset();
    return;
  }
  // No backend yet, so hand it back rather than pretending it was sent.
  const text = `Elsewhere suggestion\nCity: ${body.city}\nPlace: ${body.place}\nWhy: ${body.why}`;
  try {
    await navigator.clipboard.writeText(text);
    note.textContent = "Copied to your clipboard — send it over and it'll get added.";
  } catch {
    note.textContent = text;
  }
});

/* Two screens, one question each. `showSetup` is the only thing that decides
   which is on. */
let onSetup = true;

function showSetup(on) {
  onSetup = on;
  // Only touch visibility when home is the page being shown; otherwise
  // showPage owns it and would be fighting us.
  if (page === "home") {
    document.getElementById("setup").hidden = !on;
    document.getElementById("app").hidden = on;
  }
  if (on) stopDemo();
}

async function boot() {
  // A shared link carries its whole state and skips the picker — someone who
  // was sent a result should land on it, not on a form.
  const u = readURL();
  page = PAGES.includes(u.page) ? u.page : "home";
  if (u.city) home = u.city;
  if (u.dest) dest = u.dest;
  q = u.q; cat = u.cat;

  const cities = await (await fetch("/api/cities")).json();
  // Remembered choice first, then the time-zone guess, then the fallback.
  if (!home || !cities.includes(home)) home = guessFromZone(cities) || cities[0];

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
  showPage(page, false);
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

document.getElementById("swapbtn").addEventListener("click", async e => {
  const btn = e.currentTarget;
  const from = home, to = dest;
  if (!to) return;
  btn.classList.add("spin");
  setTimeout(() => btn.classList.remove("spin"), 520);
  home = to;
  document.getElementById("srcsel").value = home;
  drawSrc();
  dest = from;                 // kept if the reversed pair exists
  await fillDestinations();
});

document.getElementById("locatebtn").addEventListener("click", e => locateMe(e.currentTarget));
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
    btn.innerHTML = `${esc(title(sel.value))}<span class="caret">\u25bc</span>`;
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
    if (!open) return;
    // Measure, then decide which way to open. On a phone the sentence wraps
    // and the picker can end up close enough to the bottom that a downward
    // menu runs past the fold and lands on the controls underneath it.
    menu.classList.remove("up");
    const r = menu.getBoundingClientRect();
    const below = innerHeight - btn.getBoundingClientRect().bottom;
    if (r.height + 20 > below) menu.classList.add("up");
    menu.scrollIntoView({ block: "nearest", behavior: "smooth" });
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
  document.querySelectorAll(".citymenu").forEach(m => {
    m.hidden = true;
    m.classList.remove("up");
  });
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
  const sh = e.target.closest(".share");
  if (sh) { shareAnswer(sh); return; }
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
