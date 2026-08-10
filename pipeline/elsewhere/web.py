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
FISH = """<svg class="decor decor-fish" viewBox="0 0 360 300" fill="none"
  stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"
  aria-hidden="true">
  <g>
    <defs><clipPath id="fa"><path d="M22.0 58.0 C68.2 22.3 152.2 22.3 232.0 58.0 C152.2 93.7 68.2 93.7 22.0 58.0Z"/></clipPath></defs>
    <g clip-path="url(#fa)">
    <path d="M47.2 -8.4 A30.7 30.7 0 0 1 47.2 53.0" fill="currentColor" stroke-width="1.28"/>
    <path d="M47.2 19.2 A30.7 30.7 0 0 1 47.2 80.6" fill="none" stroke-width="1.28"/>
    <path d="M47.2 46.9 A30.7 30.7 0 0 1 47.2 108.3" fill="none" stroke-width="1.28"/>
    <path d="M47.2 74.5 A30.7 30.7 0 0 1 47.2 135.9" fill="none" stroke-width="1.28"/>
    <path d="M66.2 5.4 A30.7 30.7 0 0 1 66.2 66.8" fill="none" stroke-width="1.28"/>
    <path d="M66.2 33.0 A30.7 30.7 0 0 1 66.2 94.4" fill="none" stroke-width="1.28"/>
    <path d="M66.2 60.7 A30.7 30.7 0 0 1 66.2 122.1" fill="none" stroke-width="1.28"/>
    <path d="M66.2 88.3 A30.7 30.7 0 0 1 66.2 149.7" fill="currentColor" stroke-width="1.28"/>
    <path d="M85.3 -8.4 A30.7 30.7 0 0 1 85.3 53.0" fill="none" stroke-width="1.28"/>
    <path d="M85.3 19.2 A30.7 30.7 0 0 1 85.3 80.6" fill="currentColor" stroke-width="1.28"/>
    <path d="M85.3 46.9 A30.7 30.7 0 0 1 85.3 108.3" fill="none" stroke-width="1.28"/>
    <path d="M85.3 74.5 A30.7 30.7 0 0 1 85.3 135.9" fill="none" stroke-width="1.28"/>
    <path d="M104.3 5.4 A30.7 30.7 0 0 1 104.3 66.8" fill="none" stroke-width="1.28"/>
    <path d="M104.3 33.0 A30.7 30.7 0 0 1 104.3 94.4" fill="none" stroke-width="1.28"/>
    <path d="M104.3 60.7 A30.7 30.7 0 0 1 104.3 122.1" fill="none" stroke-width="1.28"/>
    <path d="M104.3 88.3 A30.7 30.7 0 0 1 104.3 149.7" fill="none" stroke-width="1.28"/>
    <path d="M123.3 -8.4 A30.7 30.7 0 0 1 123.3 53.0" fill="none" stroke-width="1.28"/>
    <path d="M123.3 19.2 A30.7 30.7 0 0 1 123.3 80.6" fill="none" stroke-width="1.28"/>
    <path d="M123.3 46.9 A30.7 30.7 0 0 1 123.3 108.3" fill="currentColor" stroke-width="1.28"/>
    <path d="M123.3 74.5 A30.7 30.7 0 0 1 123.3 135.9" fill="none" stroke-width="1.28"/>
    <path d="M142.4 5.4 A30.7 30.7 0 0 1 142.4 66.8" fill="currentColor" stroke-width="1.28"/>
    <path d="M142.4 33.0 A30.7 30.7 0 0 1 142.4 94.4" fill="none" stroke-width="1.28"/>
    <path d="M142.4 60.7 A30.7 30.7 0 0 1 142.4 122.1" fill="none" stroke-width="1.28"/>
    <path d="M142.4 88.3 A30.7 30.7 0 0 1 142.4 149.7" fill="none" stroke-width="1.28"/>
    <path d="M161.4 -8.4 A30.7 30.7 0 0 1 161.4 53.0" fill="none" stroke-width="1.28"/>
    <path d="M161.4 19.2 A30.7 30.7 0 0 1 161.4 80.6" fill="none" stroke-width="1.28"/>
    <path d="M161.4 46.9 A30.7 30.7 0 0 1 161.4 108.3" fill="none" stroke-width="1.28"/>
    <path d="M161.4 74.5 A30.7 30.7 0 0 1 161.4 135.9" fill="currentColor" stroke-width="1.28"/>
    <path d="M180.4 5.4 A30.7 30.7 0 0 1 180.4 66.8" fill="none" stroke-width="1.28"/>
    <path d="M180.4 33.0 A30.7 30.7 0 0 1 180.4 94.4" fill="currentColor" stroke-width="1.28"/>
    <path d="M180.4 60.7 A30.7 30.7 0 0 1 180.4 122.1" fill="none" stroke-width="1.28"/>
    <path d="M180.4 88.3 A30.7 30.7 0 0 1 180.4 149.7" fill="none" stroke-width="1.28"/>
    <path d="M199.5 -8.4 A30.7 30.7 0 0 1 199.5 53.0" fill="none" stroke-width="1.28"/>
    <path d="M199.5 19.2 A30.7 30.7 0 0 1 199.5 80.6" fill="none" stroke-width="1.28"/>
    <path d="M199.5 46.9 A30.7 30.7 0 0 1 199.5 108.3" fill="none" stroke-width="1.28"/>
    <path d="M199.5 74.5 A30.7 30.7 0 0 1 199.5 135.9" fill="none" stroke-width="1.28"/>
    <path d="M218.5 5.4 A30.7 30.7 0 0 1 218.5 66.8" fill="none" stroke-width="1.28"/>
    <path d="M218.5 33.0 A30.7 30.7 0 0 1 218.5 94.4" fill="none" stroke-width="1.28"/>
    <path d="M218.5 60.7 A30.7 30.7 0 0 1 218.5 122.1" fill="currentColor" stroke-width="1.28"/>
    <path d="M218.5 88.3 A30.7 30.7 0 0 1 218.5 149.7" fill="none" stroke-width="1.28"/>
    <path d="M237.6 -8.4 A30.7 30.7 0 0 1 237.6 53.0" fill="currentColor" stroke-width="1.28"/>
    <path d="M237.6 19.2 A30.7 30.7 0 0 1 237.6 80.6" fill="none" stroke-width="1.28"/>
    <path d="M237.6 46.9 A30.7 30.7 0 0 1 237.6 108.3" fill="none" stroke-width="1.28"/>
    <path d="M237.6 74.5 A30.7 30.7 0 0 1 237.6 135.9" fill="none" stroke-width="1.28"/>
    <path d="M256.6 5.4 A30.7 30.7 0 0 1 256.6 66.8" fill="none" stroke-width="1.28"/>
    <path d="M256.6 33.0 A30.7 30.7 0 0 1 256.6 94.4" fill="none" stroke-width="1.28"/>
    <path d="M256.6 60.7 A30.7 30.7 0 0 1 256.6 122.1" fill="none" stroke-width="1.28"/>
    <path d="M256.6 88.3 A30.7 30.7 0 0 1 256.6 149.7" fill="currentColor" stroke-width="1.28"/>
    </g>
    <path d="M64.0 27.3 L76.6 58.0 L64.0 88.7" fill="var(--paper)" stroke="none" clip-path="url(#fa)"/>
    <path d="M64.0 27.3 L76.6 58.0 L64.0 88.7" fill="none" stroke-width="1.60"/>
    <path d="M22.0 58.0 C68.2 22.3 152.2 22.3 232.0 58.0 C152.2 93.7 68.2 93.7 22.0 58.0Z" fill="none" stroke-width="1.84"/>
    <circle cx="47.2" cy="55.1" r="12.1" fill="var(--paper)" stroke-width="1.60"/>
    <circle cx="47.2" cy="55.1" r="5.1" fill="currentColor" stroke="none"/>
    <path d="M32.5 40.1 q6.3 -12.1 15.8 -4.3" stroke-width="1.44" fill="none"/>
    <path d="M232.0 58.0 C253.0 40.1 265.6 4.4 274.0 -9.8 C261.4 33.0 261.4 83.0 274.0 125.8 C265.6 111.6 253.0 75.8 232.0 58.0Z" fill="currentColor" stroke="none"/>
  </g>
  <g>
    <defs><clipPath id="fb"><path d="M96.0 152.0 C135.6 121.4 207.6 121.4 276.0 152.0 C207.6 182.6 135.6 182.6 96.0 152.0Z"/></clipPath></defs>
    <g clip-path="url(#fb)">
    <circle cx="123.0" cy="130.0" r="5.0" stroke-width="1.12"/>
    <circle cx="139.8" cy="130.0" r="5.0" stroke-width="1.12"/>
    <circle cx="156.6" cy="130.0" r="5.0" stroke-width="1.12"/>
    <circle cx="173.4" cy="130.0" r="5.0" stroke-width="1.12"/>
    <circle cx="131.1" cy="138.8" r="5.7" stroke-width="1.12"/>
    <circle cx="147.9" cy="138.8" r="5.7" stroke-width="1.12"/>
    <circle cx="164.7" cy="138.8" r="5.7" stroke-width="1.12"/>
    <circle cx="181.5" cy="138.8" r="5.7" stroke-width="1.12"/>
    <circle cx="123.0" cy="147.6" r="6.4" stroke-width="1.12"/>
    <circle cx="139.8" cy="147.6" r="6.4" stroke-width="1.12"/>
    <circle cx="156.6" cy="147.6" r="6.4" stroke-width="1.12"/>
    <circle cx="173.4" cy="147.6" r="6.4" stroke-width="1.12"/>
    <circle cx="131.1" cy="156.4" r="6.4" stroke-width="1.12"/>
    <circle cx="147.9" cy="156.4" r="6.4" stroke-width="1.12"/>
    <circle cx="164.7" cy="156.4" r="6.4" stroke-width="1.12"/>
    <circle cx="181.5" cy="156.4" r="6.4" stroke-width="1.12"/>
    <circle cx="123.0" cy="165.2" r="5.7" stroke-width="1.12"/>
    <circle cx="139.8" cy="165.2" r="5.7" stroke-width="1.12"/>
    <circle cx="156.6" cy="165.2" r="5.7" stroke-width="1.12"/>
    <circle cx="173.4" cy="165.2" r="5.7" stroke-width="1.12"/>
    <circle cx="131.1" cy="174.0" r="5.0" stroke-width="1.12"/>
    <circle cx="147.9" cy="174.0" r="5.0" stroke-width="1.12"/>
    <circle cx="164.7" cy="174.0" r="5.0" stroke-width="1.12"/>
    <circle cx="181.5" cy="174.0" r="5.0" stroke-width="1.12"/>
    <path d="M182.4 121.4 L279.6 121.4" stroke-width="0.99"/>
    <path d="M182.4 125.8 L279.6 125.8" stroke-width="0.99"/>
    <path d="M182.4 130.1 L279.6 130.1" stroke-width="0.99"/>
    <path d="M182.4 134.5 L279.6 134.5" stroke-width="0.99"/>
    <path d="M182.4 138.9 L279.6 138.9" stroke-width="0.99"/>
    <path d="M182.4 143.3 L279.6 143.3" stroke-width="0.99"/>
    <path d="M182.4 147.6 L279.6 147.6" stroke-width="0.99"/>
    <path d="M182.4 152.0 L279.6 152.0" stroke-width="0.99"/>
    <path d="M182.4 156.4 L279.6 156.4" stroke-width="0.99"/>
    <path d="M182.4 160.7 L279.6 160.7" stroke-width="0.99"/>
    <path d="M182.4 165.1 L279.6 165.1" stroke-width="0.99"/>
    <path d="M182.4 169.5 L279.6 169.5" stroke-width="0.99"/>
    <path d="M182.4 173.9 L279.6 173.9" stroke-width="0.99"/>
    <path d="M182.4 178.2 L279.6 178.2" stroke-width="0.99"/>
    <path d="M182.4 182.6 L279.6 182.6" stroke-width="0.99"/>
    <path d="M178.8 121.4 C169.8 139.8 187.8 164.2 178.8 182.6" stroke-width="1.60"/>
    </g>
    <path d="M132.0 125.7 L142.8 152.0 L132.0 178.3" fill="var(--paper)" stroke="none" clip-path="url(#fb)"/>
    <path d="M132.0 125.7 L142.8 152.0 L132.0 178.3" fill="none" stroke-width="1.60"/>
    <path d="M96.0 152.0 C135.6 121.4 207.6 121.4 276.0 152.0 C207.6 182.6 135.6 182.6 96.0 152.0Z" fill="none" stroke-width="1.84"/>
    <circle cx="117.6" cy="149.6" r="10.4" fill="var(--paper)" stroke-width="1.60"/>
    <circle cx="117.6" cy="149.6" r="4.4" fill="currentColor" stroke="none"/>
    <path d="M105.0 136.7 q5.4 -10.4 13.5 -3.7" stroke-width="1.44" fill="none"/>
    <defs><clipPath id="fbt"><path d="M276.0 152.0 C294.0 136.7 304.8 106.1 312.0 93.9 C301.2 130.6 301.2 173.4 312.0 210.1 C304.8 197.9 294.0 167.3 276.0 152.0Z"/></clipPath></defs>
    <g clip-path="url(#fbt)">
    <path d="M276.0 90.8 L315.6 101.5" stroke-width="1.12"/>
    <path d="M276.0 103.0 L315.6 113.7" stroke-width="1.12"/>
    <path d="M276.0 115.3 L315.6 126.0" stroke-width="1.12"/>
    <path d="M276.0 127.5 L315.6 138.2" stroke-width="1.12"/>
    <path d="M276.0 139.8 L315.6 150.5" stroke-width="1.12"/>
    <path d="M276.0 152.0 L315.6 162.7" stroke-width="1.12"/>
    <path d="M276.0 164.2 L315.6 175.0" stroke-width="1.12"/>
    <path d="M276.0 176.5 L315.6 187.2" stroke-width="1.12"/>
    <path d="M276.0 188.7 L315.6 199.4" stroke-width="1.12"/>
    <path d="M276.0 201.0 L315.6 211.7" stroke-width="1.12"/>
    <path d="M276.0 213.2 L315.6 223.9" stroke-width="1.12"/>
    </g>
    <path d="M276.0 152.0 C294.0 136.7 304.8 106.1 312.0 93.9 C301.2 130.6 301.2 173.4 312.0 210.1 C304.8 197.9 294.0 167.3 276.0 152.0Z" fill="none" stroke-width="1.68"/>
  </g>
  <g>
    <defs><clipPath id="fc"><path d="M40.0 238.0 C73.0 212.5 133.0 212.5 190.0 238.0 C133.0 263.5 73.0 263.5 40.0 238.0Z"/></clipPath></defs>
    <g clip-path="url(#fc)">
    <path d="M14.5 207.4 L75.7 268.6" stroke-width="1.20"/>
    <path d="M14.5 268.6 L75.7 207.4" stroke-width="1.20"/>
    <path d="M26.2 207.4 L87.4 268.6" stroke-width="1.20"/>
    <path d="M26.2 268.6 L87.4 207.4" stroke-width="1.20"/>
    <path d="M38.0 207.4 L99.2 268.6" stroke-width="1.20"/>
    <path d="M38.0 268.6 L99.2 207.4" stroke-width="1.20"/>
    <path d="M49.7 207.4 L110.9 268.6" stroke-width="1.20"/>
    <path d="M49.7 268.6 L110.9 207.4" stroke-width="1.20"/>
    <path d="M61.4 207.4 L122.6 268.6" stroke-width="1.20"/>
    <path d="M61.4 268.6 L122.6 207.4" stroke-width="1.20"/>
    <path d="M73.2 207.4 L134.4 268.6" stroke-width="1.20"/>
    <path d="M73.2 268.6 L134.4 207.4" stroke-width="1.20"/>
    <path d="M84.9 207.4 L146.1 268.6" stroke-width="1.20"/>
    <path d="M84.9 268.6 L146.1 207.4" stroke-width="1.20"/>
    <path d="M96.6 207.4 L157.8 268.6" stroke-width="1.20"/>
    <path d="M96.6 268.6 L157.8 207.4" stroke-width="1.20"/>
    <path d="M108.3 207.4 L169.5 268.6" stroke-width="1.20"/>
    <path d="M108.3 268.6 L169.5 207.4" stroke-width="1.20"/>
    <path d="M120.1 207.4 L181.3 268.6" stroke-width="1.20"/>
    <path d="M120.1 268.6 L181.3 207.4" stroke-width="1.20"/>
    <path d="M131.8 207.4 L193.0 268.6" stroke-width="1.20"/>
    <path d="M131.8 268.6 L193.0 207.4" stroke-width="1.20"/>
    <path d="M143.5 207.4 L204.7 268.6" stroke-width="1.20"/>
    <path d="M143.5 268.6 L204.7 207.4" stroke-width="1.20"/>
    <path d="M155.3 207.4 L216.5 268.6" stroke-width="1.20"/>
    <path d="M155.3 268.6 L216.5 207.4" stroke-width="1.20"/>
    <path d="M167.0 207.4 L228.2 268.6" stroke-width="1.20"/>
    <path d="M167.0 268.6 L228.2 207.4" stroke-width="1.20"/>
    <path d="M178.7 207.4 L239.9 268.6" stroke-width="1.20"/>
    <path d="M178.7 268.6 L239.9 207.4" stroke-width="1.20"/>
    <path d="M190.5 207.4 L251.7 268.6" stroke-width="1.20"/>
    <path d="M190.5 268.6 L251.7 207.4" stroke-width="1.20"/>
    <path d="M202.2 207.4 L263.4 268.6" stroke-width="1.20"/>
    <path d="M202.2 268.6 L263.4 207.4" stroke-width="1.20"/>
    <path d="M213.9 207.4 L275.1 268.6" stroke-width="1.20"/>
    <path d="M213.9 268.6 L275.1 207.4" stroke-width="1.20"/>
    <path d="M225.6 207.4 L286.8 268.6" stroke-width="1.20"/>
    <path d="M225.6 268.6 L286.8 207.4" stroke-width="1.20"/>
    </g>
    <path d="M70.0 216.1 L79.0 238.0 L70.0 259.9" fill="var(--paper)" stroke="none" clip-path="url(#fc)"/>
    <path d="M70.0 216.1 L79.0 238.0 L70.0 259.9" fill="none" stroke-width="1.60"/>
    <path d="M40.0 238.0 C73.0 212.5 133.0 212.5 190.0 238.0 C133.0 263.5 73.0 263.5 40.0 238.0Z" fill="none" stroke-width="1.84"/>
    <circle cx="58.0" cy="236.0" r="8.7" fill="var(--paper)" stroke-width="1.60"/>
    <circle cx="58.0" cy="236.0" r="3.6" fill="currentColor" stroke="none"/>
    <path d="M47.5 225.2 q4.5 -8.7 11.2 -3.1" stroke-width="1.44" fill="none"/>
    <path d="M190.0 238.0 C205.0 225.2 214.0 199.8 220.0 189.6 C211.0 220.2 211.0 255.8 220.0 286.4 C214.0 276.2 205.0 250.8 190.0 238.0Z" fill="currentColor" stroke="none"/>
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


def page_html() -> str:
    """The page with the mark substituted into every slot that wants it."""
    return (
        PAGE.replace("__LOOP_INLINE__", LOOP.format(cls="loop"))
        .replace("__LOOP_NEXT__", ARROW.format(cls="go-arrow"))
        .replace("__LOOP_EMPTY__", LOOP.format(cls="loop-empty"))
        .replace("__LOOP_SPIN__", LOOP.format(cls="loop-spin"))
        .replace("__SUPABASE__", json.dumps(supabase_config()))
        .replace("__DECOR__", FISH + BALLOONS)
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
  /* The one accent, and it has to survive both jobs: white text sitting on
     it (buttons) and it sitting on white (links, the brand). #33788F clears
     AA in both directions at 4.7:1 — the bright turquoises people reach for
     first are around 2:1 on white, which is a decoration, not a colour you
     can put a word in. */
  --accent: #3985A6;
  /* #3985A6 is 4.13:1 on white — right for display type and the mark, a
     shade under AA for 13px labels and for white text sitting on it. This is
     the same hue taken down until both of those clear (4.97:1), and it is
     what fills buttons and sets small links. */
  --accent-deep: #33788F;
  --accent-soft: #E8F2F7;
  --on-accent: #FFFFFF;
  --pink: #3985A6; --pink-deep: #33788F; --on-pink: #FFFFFF;
  --turquoise: #3985A6;
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
  /* Brighter cut for the dark ground: 10:1 there, where the light-mode
     turquoise would sit at 4:1 and read as muddy. */
  --accent: #6BB6D4; --accent-deep: #7FC2DC; --accent-soft: #16303B;
  --on-accent: #10201E;
  --pink: #6BB6D4; --pink-deep: #7FC2DC; --on-pink: #10201E;
  --turquoise: #6BB6D4; --gold: #FFC53D; --emerald: #35C89B;
  --chip: #242424;
  --bar: #121212; --bar-ink: #F4F4F4; --bar-line: #303030;
  --bar-dim: #9A9A9A; --bar-field: #1D1D1D;
  --shadow: 0 1px 2px rgba(0,0,0,.5), 0 4px 12px rgba(0,0,0,.45);
  --lift:   0 2px 4px rgba(0,0,0,.55), 0 10px 28px rgba(0,0,0,.6);
}
* { box-sizing: border-box; }
/* Nothing had a visible focus ring, so the whole app was unusable by
   keyboard. :focus-visible keeps it off for mouse users. */
:focus-visible {
  outline: 2px solid var(--accent); outline-offset: 3px; border-radius: 4px;
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
/* Deliberately no z-index here. A positioned ancestor with one creates a
   stacking context, which traps the menu's z-index inside it — the menu was
   losing to a plain button that came later in the document, despite asking
   for 200. Leaving this at auto lets the menu compete in the page's own
   context, where it wins. */
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
/* The menu outranks everything around it. It sits inside the sentence, which
   has a button immediately after it and another below, and a dropdown that
   loses to either is worse than no dropdown. */
.citymenu {
  position: absolute; top: calc(100% + 8px); left: 0; z-index: 200;
  min-width: 100%; padding: 7px; border-radius: 18px;
  background: var(--panel); box-shadow: var(--lift);
  display: flex; flex-direction: column; gap: 2px;
  transform-origin: top center;
  animation: menuin .18s var(--spring);
}
.citymenu[hidden] { display: none; }
/* Flipped when the viewport has no room underneath — see openCityMenu. */
.citymenu.up { top: auto; bottom: calc(100% + 8px); transform-origin: bottom center; }
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
  position: absolute; top: calc(100% + 10px); left: 0; right: 0; z-index: 60;
  padding: 8px; border-radius: 18px; background: var(--panel);
  box-shadow: var(--lift), inset 0 0 0 1px var(--hair);
  max-height: 340px; overflow-y: auto; text-align: left;
}
.suggest[hidden] { display: none; }
.suggest button {
  display: block; width: 100%; text-align: left; padding: 10px 14px;
  border-radius: 12px; background: none; color: var(--ink);
  font-size: 15px; font-weight: 600;
}
.suggest button .cat { font-size: 13px; font-weight: 400; color: var(--faint); margin-left: 10px; }
.suggest button:hover { background: var(--sunk); }
.suggest button.on { background: var(--accent); color: var(--on-accent); }
.suggest button.on .cat { color: var(--on-accent); opacity: .85; }
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
  display: inline-flex; align-items: center; gap: 9px;
  font-size: 23px; font-weight: 800; letter-spacing: -0.035em;
  color: var(--accent); line-height: 1; align-self: center;
}
button.brand:hover { color: var(--accent-deep); }
/* The tile's cream ground is invisible on white and muddy on the dark
   theme, so the mark wears the same rounding as the rest of the chrome and
   picks up a faint edge to sit on. */
button.brand img { border-radius: 8px; box-shadow: inset 0 0 0 1px var(--hair); }
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
.stage.typing .decor { display: none; }
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

.browse { padding: 56px 40px 60px; text-align: left; max-width: 1180px; margin: 0 auto; }
.browse[hidden] { display: none; }
.browseh {
  font-size: clamp(24px, 3vw, 34px); font-weight: 800; letter-spacing: -0.03em;
  margin: 0 0 28px; color: var(--ink);
}
.cats { max-width: 900px; margin: 0 auto; }
.cats {
  display: grid; gap: 12px; max-width: none; margin: 0;
  grid-template-columns: repeat(auto-fill, minmax(210px, 1fr));
}
.cicon {
  width: 34px; height: 34px; color: var(--accent); margin-bottom: 12px;
  transition: transform .25s var(--spring);
}
.cats button:hover .cicon { transform: translateY(-2px) rotate(-3deg); }
.clabel { display: block; }
.cats button {
  font-size: 17px; font-weight: 600; padding: 20px 22px 22px; text-align: left;
  background: var(--panel); color: var(--ink); border-radius: 16px;
  box-shadow: inset 0 0 0 1px var(--hair);
  display: flex; flex-direction: column; align-items: flex-start; gap: 4px;
}
.cats button:hover { box-shadow: var(--lift); transform: translateY(-2px); }
.cats button:active { transform: none; }
.cats button .n { font-size: 14px; font-weight: 400; color: var(--dim); margin: 0; }

/* ─── Results ─────────────────────────────────────────────────────────── */
main { max-width: 860px; margin: 0 auto; padding: 20px 24px 140px; }
.crumb {
  max-width: 1180px; margin: 0 auto; padding: 26px 40px 0;
  display: flex; align-items: baseline; gap: 14px;
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
/* The translation, read top to bottom on a phone and left to right on a
   desk: what you know, the loop, what it becomes. */
.tr {
  display: grid; grid-template-columns: 1fr auto 1fr; gap: 18px;
  align-items: center; margin-bottom: 18px;
}
.side { min-width: 0; }
.side:last-child { text-align: right; }
.pname {
  font-size: 15px; font-weight: 600; color: var(--dim); letter-spacing: -0.01em;
  line-height: 1.25;
}
.pcity {
  font-size: 12.5px; color: var(--faint); margin-top: 3px;
  text-transform: uppercase; letter-spacing: .08em; font-weight: 600;
}
.arrowcol { display: flex; flex-direction: column; align-items: center; gap: 5px; }
.loop-empty { width: 34px; height: 34px; color: var(--accent); }
.elsew {
  font-size: 11.5px; color: var(--faint); white-space: nowrap;
  letter-spacing: .04em;
}
.tr .answer { margin: 0; }

/* Said in words, because the number underneath is self-rated. */
.strength {
  display: inline-block; font-size: 12.5px; font-weight: 600;
  padding: 5px 12px; border-radius: 999px; margin-bottom: 10px;
  background: var(--accent-soft); color: var(--accent-deep);
}
.strength.mid { background: var(--sunk); color: var(--dim); }
.strength.lo { background: var(--sunk); color: var(--faint); }
.roleline {
  font-size: 13.5px; color: var(--dim); margin-bottom: 12px;
}

@media (max-width: 700px) {
  .tr { grid-template-columns: 1fr; gap: 10px; text-align: left; }
  .side:last-child { text-align: left; }
  .arrowcol { flex-direction: row; align-self: flex-start; gap: 9px; }
  .loop-empty { width: 26px; height: 26px; }
}

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
.acts a, .acts .save, .acts .share {
  font-size: 14px; font-weight: 600; padding: 11px 16px;
  border-radius: 999px; background: var(--chip); color: var(--accent-deep);
  text-decoration: none; display: inline-flex; align-items: center; gap: 6px;
  transition: background .2s, color .2s, transform .22s var(--spring);
}
.acts a:hover, .acts .save:hover, .acts .share:hover {
  background: var(--pink-deep); color: var(--on-pink); transform: translateY(-2px);
}
/* A save is yours, not the site's — pink marks it as the one thing on the
   page you put there. */
.acts .share { background: var(--chip); color: var(--accent-deep); }
.acts .share.done { background: var(--accent); color: var(--on-accent); }
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
/* ─── Sign-in sheet ───────────────────────────────────────────────────── */
.sheet {
  position: fixed; inset: 0; z-index: 90; display: grid; place-items: center;
  background: rgba(20,20,20,.45); padding: 24px;
}
.sheet[hidden] { display: none; }
.sheetbox {
  background: var(--panel); border-radius: 20px; padding: 34px 32px 28px;
  max-width: 440px; width: 100%; box-shadow: var(--lift); position: relative;
}
.sheetbox h2 { font-size: 24px; font-weight: 800; letter-spacing: -0.03em; margin: 0 0 10px; }
.sheetbox p { color: var(--dim); font-size: 15px; margin: 0 0 20px; }
.sheetx {
  position: absolute; top: 14px; right: 14px; width: 34px; height: 34px;
  background: none; color: var(--dim); font-size: 22px; line-height: 1;
  display: grid; place-items: center;
}
.sheetx:hover { background: var(--sunk); }
.sheetbox input {
  width: 100%; font-size: 16px; padding: 15px 18px; border-radius: 12px;
  background: var(--panel); box-shadow: inset 0 0 0 1px var(--hair-2);
}
.next.wide { width: 100%; height: auto; border-radius: 12px; padding: 15px; font-size: 16px; font-weight: 600; }
.sheetnote { font-size: 14px; margin: 14px 0 0 !important; min-height: 20px; }

/* ─── Verification, on the card ───────────────────────────────────────── */
.verify { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-top: 14px; }
.verify[hidden] { display: none; }
.vbtn {
  font-size: 13.5px; font-weight: 600; padding: 9px 14px; border-radius: 999px;
  background: none; color: var(--dim); box-shadow: inset 0 0 0 1px var(--hair-2);
}
.vbtn:hover { color: var(--ink); box-shadow: inset 0 0 0 1px var(--ink); }
.vbtn[aria-pressed=true] { background: var(--accent); color: var(--on-accent); box-shadow: none; }
.vbtn.no[aria-pressed=true] { background: var(--dim); color: var(--paper); }
.tally { font-size: 13px; color: var(--faint); }

.nudge {
  position: fixed; left: 50%; bottom: 30px; transform: translate(-50%, 14px);
  background: var(--ink); color: var(--paper); padding: 13px 22px;
  border-radius: 999px; font-size: 15px; font-weight: 600; z-index: 80;
  opacity: 0; transition: opacity .3s, transform .3s var(--spring);
  box-shadow: var(--lift);
}
.nudge.in { opacity: 1; transform: translate(-50%, 0); }

.empty { text-align: center; color: var(--dim); padding: 80px 20px; font-size: 17px; }
.empty p { margin: 18px 0 0; }
.loop-spin { width: 54px; height: 54px; color: var(--hair-2); }
.more { text-align: center; color: var(--dim); font-size: 14px; padding: 16px 20px 48px; }

/* ─── Nav ─────────────────────────────────────────────────────────────── */
.topnav {
  position: sticky; top: 0; z-index: 300; padding: 14px 20px 10px;
  background: color-mix(in srgb, var(--paper) 88%, transparent);
  backdrop-filter: saturate(1.3) blur(10px);
  -webkit-backdrop-filter: saturate(1.3) blur(10px);
}
.navrow {
  display: flex; gap: 8px; justify-content: center; flex-wrap: wrap;
  box-shadow: inset 0 0 0 1.5px var(--ink); border-radius: 999px;
  padding: 5px; width: max-content; margin: 0 auto; background: var(--paper);
}
.navpill {
  font-size: 14.5px; font-weight: 500; padding: 7px 18px; border-radius: 999px;
  background: none; color: var(--ink);
  box-shadow: inset 0 0 0 1.5px var(--ink);
  text-decoration: underline; text-underline-offset: 3px;
  text-decoration-thickness: 1px;
}
.navpill:hover { background: var(--sunk); }
/* The page you're on is the one that isn't a link any more. */
.navpill[aria-current=page] { text-decoration: none; background: var(--paper); font-weight: 600; }
.navpill:not([aria-current=page]):hover { text-decoration-thickness: 2px; }

@media (max-width: 560px) {
  .navrow { gap: 5px; padding: 4px; }
  .navpill { font-size: 13px; padding: 6px 12px; }
}

/* ─── Simple pages ────────────────────────────────────────────────────── */
.page { max-width: 720px; margin: 0 auto; padding: 60px 24px 120px; }
.page[hidden] { display: none; }
.page h1 { font-size: clamp(30px, 4vw, 44px); font-weight: 800; letter-spacing: -0.035em; margin: 0 0 14px; }
.page .lede { font-size: 18px; color: var(--dim); margin: 0 0 36px; line-height: 1.55; }
.page h2 { font-size: 19px; font-weight: 700; margin: 34px 0 12px; }
.page label { display: block; font-size: 14px; font-weight: 600; margin: 0 0 6px; }
.page input, .page textarea, .page select {
  width: 100%; font: inherit; font-size: 16px; padding: 13px 16px; border-radius: 12px;
  background: var(--panel); color: var(--ink); box-shadow: inset 0 0 0 1px var(--hair-2);
  margin-bottom: 16px;
}
.page textarea { min-height: 120px; resize: vertical; }
.page .btn {
  font-size: 16px; font-weight: 600; padding: 14px 26px; border-radius: 999px;
  background: var(--accent-deep); color: var(--on-accent);
}
.page .btn:hover { filter: brightness(1.08); }
.page .quiet { color: var(--dim); font-size: 15px; }
.savedgrid { display: grid; gap: 10px; }
.savedrow {
  display: flex; align-items: baseline; gap: 10px; padding: 14px 16px;
  border-radius: 14px; box-shadow: inset 0 0 0 1px var(--hair);
}
.savedrow b { font-size: 16px; }
.savedrow span { color: var(--faint); font-size: 13.5px; }
.savedrow a { margin-left: auto; color: var(--accent-deep); font-size: 14px; font-weight: 600; }

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
  opacity: .09; z-index: 0;
}
:root[data-theme="dark"] .decor { opacity: .14; }
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

/* One sentence, not two form fields. The cities are words in it that happen
   to open a menu, and the loop between them is the verb: this, translated
   into that. Nothing here is boxed, because a box would make it a form
   again. */
.madlib {
  display: flex; align-items: center; justify-content: center; flex-wrap: wrap;
  gap: 6px 14px; margin: 0 auto 34px; max-width: 900px;
  font-size: clamp(22px, 3vw, 34px); font-weight: 600; letter-spacing: -0.02em;
  color: var(--dim);
}
.madlib .lead { color: var(--dim); }
.madlib .citypick { display: inline-flex; }
.madlib .citybtn {
  font-size: inherit; font-weight: 800; letter-spacing: -0.03em;
  color: var(--ink); background: none; box-shadow: none;
  padding: 2px 4px; gap: 8px; border-radius: 8px;
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
  background: none; padding: 6px; border-radius: 999px; display: grid;
  place-items: center; color: var(--accent);
}
.swap:hover { background: var(--accent-soft); }
.swap.spin .loop { animation: flip .5s var(--spring); }
@keyframes flip { from { transform: rotate(0) scale(1); } 50% { transform: rotate(180deg) scale(.8); } to { transform: rotate(360deg) scale(1); } }

/* Offered, never taken. The prompt only fires if this is pressed. */
.locate {
  display: block; margin: 22px auto 0; background: none; color: var(--dim);
  font-size: 14px; font-weight: 600; padding: 8px 14px; border-radius: 999px;
  box-shadow: inset 0 0 0 1px var(--hair-2);
}
.locate:hover { color: var(--ink); box-shadow: inset 0 0 0 1px var(--ink); }
.locate[hidden] { display: none; }

/* The verb, again — the same mark, filled in and clickable. */
.next {
  width: 62px; height: 62px; padding: 0; border-radius: 999px;
  background: var(--accent); color: var(--on-accent);
  display: grid; place-items: center; box-shadow: none; flex: none;
}
.next:hover { background: var(--accent-deep); transform: scale(1.05); box-shadow: none; }
.next:active { transform: scale(.96); }
.go-arrow { width: 26px; height: 26px; }

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
  /* Three lines on a phone. Left to wrap on its own, "an H-E-B," lands
     alone on line two as a stranded fragment; breaking deliberately puts the
     place name at the head of its own line where it reads as the subject. */
  /* The pill is a row of segments, which needs width it doesn't have here.
     Stack it: each question gets a full-width row, the divider becomes a
     rule between them, and the verb spans the bottom. */
  .pair {
    flex-direction: column; align-items: stretch; border-radius: 24px;
    padding: 8px; gap: 2px; max-width: 420px;
  }
  .leg { padding: 14px 18px; border-radius: 18px; }
  .leg + .leg { box-shadow: inset 0 1px 0 var(--hair); }
  .leg .citybtn { font-size: 17px; }
  .next { width: 54px; height: 54px; padding: 0; }
  .setup { padding: 28px 20px 48px; }
  .setup .sub { font-size: 16px; margin-bottom: 30px; }
  .setup .pitch .hl2 { display: block; }
  .setup .pitch .l1 { white-space: normal; }
  .pitch { margin-bottom: 24px; font-size: clamp(28px, 8.4vw, 40px); }
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
    We\'ll find the counterparts.</p>

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
<section class="page" id="page-account" hidden>
  <h1>Your account</h1>
  <p class="lede" id="acctlede"></p>
  <div id="acctauth"></div>
  <h2>Saved places</h2>
  <div class="savedgrid" id="acctsaved"></div>
  <h2>What you seem to like</h2>
  <p class="quiet" id="accttaste"></p>
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

function toggleSave(city, name, from, links) {
  const k = savedKey(city, name);
  if (saved.has(k)) saved.delete(k);
  else saved.set(k, { city, name, from, links, at: Date.now() });
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
      return `<div class="city">
        <div class="tr">
          <div class="side">
            <div class="pname">${esc(m.name)}</div>
            <div class="pcity">${esc(title(S.source))}</div>
          </div>
          <div class="arrowcol">
            __LOOP_EMPTY__
            <span class="elsew">elsewhere in ${esc(title(t))}</span>
          </div>
          <div class="side">
            <div class="answer"><span>${esc(top.name)}</span></div>
            <div class="pcity">${esc(title(t))}</div>
          </div>
        </div>
        ${strength(top)}
        ${roles ? `<div class="roleline">${roles}</div>` : ""}
        <div class="why big">${esc(top.reasoning)}</div>
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

function renderAccount() {
  const rows = [...saved.values()].sort((a, b) => b.at - a.at);
  document.getElementById("acctlede").textContent = ACCOUNTS
    ? (me ? `Signed in as ${me.email}.` : "Saves live on this device. Sign in to keep them and to verify matches.")
    : "Saves live on this device — no account needed, and nothing leaves your browser.";

  const auth = document.getElementById("acctauth");
  auth.innerHTML = !ACCOUNTS ? ""
    : me ? `<button class="btn" id="signoutbtn">Sign out</button>`
         : `<button class="btn" id="signinopen">Sign in with email</button>`;

  document.getElementById("acctsaved").innerHTML = rows.length
    ? rows.map(r => `<div class="savedrow"><b>${esc(r.name)}</b>
        <span>${esc(title(r.city))}${r.from ? ` \u00b7 your ${esc(r.from)}` : ""}</span>
        ${r.links && r.links.map ? `<a href="${esc(r.links.map)}" target="_blank" rel="noopener noreferrer">Map</a>` : ""}
      </div>`).join("")
    : `<p class="quiet">Nothing saved yet. Star an answer and it turns up here.</p>`;

  // A read of the saves rather than a profile: the categories they fall into,
  // said plainly, with no claim to know more than that.
  const groups = {};
  for (const r of rows) {
    const m = S && S.matches.find(x => x.name === r.from);
    if (m) groups[groupOf(m)] = (groups[groupOf(m)] || 0) + 1;
  }
  const top = Object.entries(groups).sort((a, b) => b[1] - a[1]).slice(0, 4).map(([k]) => k.toLowerCase());
  document.getElementById("accttaste").textContent = rows.length < 3
    ? "Save a few more and there'll be something to say here."
    : `Mostly ${top.join(" \u00b7 ")}.`;
}

document.querySelector(".topnav").addEventListener("click", e => {
  const b = e.target.closest(".navpill");
  if (b) showPage(b.dataset.page);
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
