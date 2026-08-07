# Elsewhere

Find the parallel places in an unfamiliar city.

You know your city by its places, not its categories. "Grocery store" isn't
what you want in Chicago — you want *your* H-E-B. Elsewhere maps a place you
love in one city to its closest equivalent somewhere else.

## Example

> I'm from Austin, traveling to Chicago.

| Austin | Chicago equivalent |
| --- | --- |
| H-E-B | Mariano's |
| Lifetime Fitness | East Bank Club |
| Torchy's Tacos | Big Star |

## The core problem

A good match isn't the nearest category match — it's the place that occupies
the same *role* for locals. Dimensions that matter:

- **Category** — grocery, gym, taco joint
- **Price tier** — what it costs relative to local alternatives
- **Cultural role** — regional icon, weekend ritual, late-night default
- **Local vs. national** — a chain that exists in both cities is a boring answer
- **Vibe** — who goes there, and why they're loyal to it

## Status

Early. Nothing built yet — defining the matching model first.

## Roadmap

- [ ] Decide on the place-data source (Google Places, Foursquare, OSM, curated)
- [ ] Define the similarity model across the dimensions above
- [ ] Seed a hand-curated Austin ↔ Chicago set as ground truth
- [ ] Thin API: `GET /equivalent?place=...&city=...`
- [ ] Minimal web UI
