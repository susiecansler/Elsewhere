# Hosting strategy

Written while adding geolocation, because that feature forced the question:
should any of this be resolved on a server?

## What Elsewhere actually is now

A single HTML page and a pile of JSON derived from files in this repo. There
is no database, no login, no write path, and no per-visitor state on the
server — saves live in `localStorage`, and the geolocation feature was built
so the visitor's coordinates never leave their device.

That matters more than it sounds. **The product no longer needs a server.**
Every endpoint is a pure function of files that change only when a batch is
generated:

| Endpoint | What it really is |
|---|---|
| `/` | one static HTML file |
| `/api/state?source=X` | one static JSON file per city (6 of them) |
| `/api/cities`, `/api/geo` | two small static JSON files |
| `/favicon.svg` | one static file |
| `/healthz` | only exists because Render wants a health check |

The FastAPI app is doing real work in exactly one place: gzip. A CDN does
that for free.

## Where it runs today

Render, Starter plan, **$7/month**, with a 1 GB persistent disk attached.

The disk is dead weight. It was mounted to hold reviewer judgments back when
the site collected them; that flow moved to the CLI and the write endpoints
were deleted. Nothing writes to it now.

Cold starts are also why the plan is Starter rather than Free — Render idles
free instances, and a first visitor waiting ~30 seconds for a link a friend
sent them is the worst possible first impression.

## Recommendation, in order

### 1. Static export to a CDN — $0/month

Add an `elsewhere export` command that writes the same responses to `dist/`
as files, then point Cloudflare Pages (or Netlify) at the repo.

- **Cost:** nothing, at any traffic level either of us should expect.
- **Speed:** served from an edge node near the visitor rather than from
  Oregon. Tokyo currently pays a round trip across the Pacific for every
  request.
- **Cold starts:** gone, because there is no process to wake.
- **Ceiling:** Cloudflare's free tier is unmetered bandwidth. "Moderately
  successful" is not a number that troubles it. Genuinely viral is fine too.
- **Cost of the change:** perhaps an hour. The export is a loop over the six
  cities calling functions that already exist.

The deploy story stays the same as today — push to `main`, the host builds.

### 2. Keep Render, drop the disk — $7/month

If it's not worth touching, at minimum remove the disk from `render.yaml`.
It's storing nothing.

### 3. Only if the product grows a server-shaped feature

Accounts, community submissions, saved lists that follow you between
devices, or anything that writes. Then the choice is:

- **Cloudflare Workers + D1** — stays on the same edge platform, generous
  free tier, and the static pages keep working exactly as they do.
- **Back to a small always-on box** (Render, Fly) if the write path gets
  complicated enough that SQL-at-the-edge is a fight.

Don't pre-build for this. The migration from static to static-plus-Workers is
additive, not a rewrite.

## Two dependencies worth watching

**Map tiles.** The cards load raster tiles from CARTO's public basemap CDN,
no key, no account. That is fine at our traffic and is explicitly not
guaranteed at scale — CARTO's terms allow non-commercial use with attribution
and no volume commitment. If Elsewhere gets popular the honest options are a
keyed provider on a paid tier (Mapbox, MapTiler, Stadia) or self-hosted
tiles. The failure mode is graceful: tiles stop loading, the pin and the
"Map & reviews" link still work.

**Anthropic API.** Only used offline to generate a corpus. It is a
per-city-pair capital cost, roughly **$1.50 per direction**, not a running
cost. Traffic does not touch it. Adding a seventh city is about $18 at the
current six; the total grows quadratically, which is the real argument for
being deliberate about which cities get added.

## What this costs to run, honestly

| Scenario | Monthly |
|---|---|
| Today (Render Starter + unused disk) | ~$7 |
| Static on Cloudflare Pages | $0 |
| Static + custom domain | ~$1 (domain amortised) |
| Static + Workers, if writes arrive | $0 until real volume |

The corpus itself — six cities, thirty directions, 3,420 matches — has
already been paid for. Serving it should not be a recurring bill.
