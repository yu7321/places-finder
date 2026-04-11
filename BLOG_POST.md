---
title: "Building a multi-source agriturismo finder for a Salento beach trip"
date: 2026-04-10
tags: [python, scraping, google-places, folium, travel]
---

I was planning a week on the Ionian and Adriatic coasts of Salento and ran
into a familiar problem: there is no good search engine for *agriturismi* —
the family-run Italian farm stays that are usually nicer (and cheaper, and
less depressing) than the resort hotels around them. Booking sites bury them
in their generic hotel category. Google Maps shows some, but the results
shift wildly as you pan, the official "farmstay" type catches only half of
them, and Google's phone numbers go through their mediated proxy rather than
ringing the owner's mobile.

I wanted a single map I could open offline on my phone with every agriturismo
inside an 11-km radius of three beaches I cared about, sorted by rating, with
the actual owner's number. So I built one. Here's what I learned along the
way.

## Tool 1: Google Places, with five queries instead of one

The first iteration just hit the new Places API once with the query
`agriturismo` and a 30 km bias circle. Results were thin — maybe 8–12 places
per area. The fix was to fan out across multiple queries:

```yaml
search_queries:
  - "agriturismo"
  - "agriturismo farm stay"
  - "agriturismo bed and breakfast"
  - "agriturismo con camere"
  - "agriturismo con piscina"
  - "azienda agricola agriturismo"
  - "masseria agriturismo"
```

Each query returns at most 20 places, and they overlap heavily, but the
union is much larger than any single query. After deduping by `place_id` I
went from 12 to about 35 properties around Punta Prosciutto alone.

The other surprise: Google's official type for these places is `farmstay`
(one word, no space), and many real agriturismi have neither "agriturismo"
nor "farmstay" in their displayed name — only in the type list. So the
keyword filter I run after the search has to look at both the name and the
type list, and accept any of `farmstay`, `agriturismo`, `agritourism`,
`farm stay`, or `fattoria`.

The bigger lesson, though, was about regional vocabulary. Italian farm
stays almost never call themselves "agriturismo" on their sign — the
displayed name is the historical building type. In Puglia (where I was
actually going), the word is *masseria* — a fortified farmhouse, much
more common than "agriturismo" as the leading noun. In Tuscany you want
*casale* and *podere*. In Sicily, *baglio*. Sardinia has *stazzo*. The
Itria valley has *trullo*, the cone-roofed rural buildings that ended up
on every regional postcard. None of these get found by a query for
`"agriturismo"`. They get found by queries for the building types
themselves.

The fix is to search standalone for each regional term — *masseria*,
*casale*, *podere*, *tenuta*, *borgo*, *baglio*, *trullo* — and to add
those tokens to the keyword allowlist so converted-farmhouse properties
survive the post-filter even when Google tags them with the generic
`lodging` type. Yes, this also lets through the luxury hotels in
restored masserie (Masseria San Domenico, Borgo Egnazia, etc.), but
those are usually exactly what you want for a farm-stay trip — they're
the same buildings, just with better service and a bigger pool. The
shipped config does both halves of this and the result for Lecce
roughly tripled compared to a generic-only query set.

I set the request `languageCode` to `it` so review text comes back in
Italian where the original review was Italian, which keeps the "first 5
reviews" the API returns more relevant for places in Italy.

## Tool 2: Email scraping the owner websites

Google gives me a website link for most places but no email. Italian
agriturismi almost always have a contact form or impressum page on their own
site, so I followed each `websiteUri`, fetched the homepage and the typical
contact pages (`/contatti`, `/contact`, `/impressum`, `/info`), and ran a
regex against the HTML.

```python
EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
```

Naive, but effective enough — when the page actually has an email, the regex
catches it. When the email is rendered as a JavaScript-protected anchor or
an image, I miss it, and that's fine; this is a best-effort enrichment, not
a critical path.

## Tool 3: Sitemap-driven scraping of agriturismo.it

After the first map, I noticed Google Places was missing properties I knew
existed — most embarrassingly `Agriturismo Residenza Gemma`, which has been
operating for years and which has 20+ Google reviews on its own page. Google
Places' search index has clear gaps, especially for places that don't pay to
keep their listing high in the local pack.

I went looking for a complementary source and landed on `agriturismo.it`.
The listing pages are JavaScript-driven and the API endpoints are behind
`robots.txt`'s `Disallow: /api/`, but the per-property detail pages are
public, server-rendered, and indexed in a regular XML sitemap:

```
https://www.agriturismo.it/sitemap.xml
  → /sitemap/details/details_0.xml
  → /sitemap/details/details_1.xml
  → /sitemap/details/details_2.xml
```

The three detail sitemaps together list ~3,000 property URLs across Italy.
Filtering by URL path to `/it/agriturismi/puglia/lecce/` gives 65 properties
in the Lecce province — exactly the slice I wanted, and notably more than
Google Places returned for the same area.

The detail pages embed everything I need in two JSON islands:

```html
<script type="application/json"
        data-rtk-endpoint="rentalOfferDetails"
        data-rtk-arg="...">{...}</script>
<script type="application/json" id="static-data-json">{...}</script>
```

The first one is the redux-toolkit cache for the booking widget, and it
contains the goldmine: `objectTitle`, `geoLocation` (lat/lon), `ownerInfo.phone`
(the **direct owner mobile**, not a proxied number), `ratings`, and
`complianceData` with the Italian regional and national tourism license codes.
The second adds the formatted address.

```python
RTK_RE = re.compile(
    r'<script type="application/json" data-rtk-endpoint="rentalOfferDetails"'
    r'[^>]*>(.*?)</script>',
    re.DOTALL,
)

def parse_detail(html: str, url: str) -> Agriturismo | None:
    rtk_m = RTK_RE.search(html)
    if not rtk_m:
        return None
    rtk = json.loads(rtk_m.group(1))

    name = rtk.get("objectTitle", "").strip()
    geo = rtk.get("geoLocation") or {}
    owner = rtk.get("ownerInfo") or {}
    ratings = rtk.get("ratings") or {}

    return Agriturismo(
        name=name,
        latitude=float(geo.get("lat") or 0),
        longitude=float(geo.get("lon") or 0),
        phone=owner.get("phone", "").strip(),
        rating=float((ratings.get("starValue") or "0").replace(",", ".")),
        user_rating_count=int(ratings.get("reviewCount") or 0),
        # ...
    )
```

65 fetches, 0.7 seconds apart, all 65 properties parsed cleanly. 42 had a
direct owner phone number. 65 had license codes. I went from one source
with known gaps to two sources I could cross-reference — and the
agriturismo.it slice for Lecce was nearly twice the size of what Google
Places returned for the same area.

## The dedup problem

Now I had 123 raw rows for Salento — 38 from the Adriatic coast Google
search, 20 from the Ionian coast Google search, 65 from agriturismo.it —
and many of them were the same physical place under slightly different
names. "MASSERIA
SALENTINA \"Costarella\"" on Google was the same business as "Masseria
Salentina" on agriturismo.it. "Agriturismo biologico Fontanelle" was just
"Fontanelle". I needed to detect and merge these without losing data from
either side.

Naive name matching worked for 5 of the obvious overlaps but missed all the
ones with renamings. Pure coordinate matching would have collapsed too much:
two genuinely different masserie can be 60 m apart in dense parts of Salento.

The solution that worked is two-tier:

```python
CLOSE_M = 50    # below this, merge regardless of name
FUZZY_M = 250   # below this, merge only if names share a significant token
```

The "significant token" check tokenizes both names, lowercases them, drops
stopwords (`agriturismo`, `masseria`, `tenuta`, `bio`, `country`, `hotel`,
`ristorante`, etc.) and short tokens, then checks for any overlap. So
"Salos Bio Agriturismo & Camping" and "Bio Agriturismo Salos" both reduce to
`{salos, camping}` and `{salos}`, which intersect — merge. "La Turrita" and
"Masseria Spartivento" reduce to `{turrita}` and `{spartivento}`, which
don't — keep separate, even though they're 60 m apart.

This caught 14 cross-source duplicates, none false. The merge function then
combines fields rather than picking a winner: phone comes from the
agriturismo.it row (direct owner), website comes from the Google row (the
real owner's site, not the agriturismo.it listing URL), reviews come from
Google (agriturismo.it doesn't expose review bodies in the initial page
load), license codes come from agriturismo.it. The merged row gets `source:
"agriturismo.it; google_places"` so the merge is visible downstream.

## The map

I render the result with [folium](https://python-visualization.github.io/folium/),
which is a thin Python wrapper around Leaflet.js that emits a fully
self-contained HTML file. No tile cache server, no API key at view time, no
backend at all — I can drop the file on a USB stick or open it offline on
my phone in airplane mode. This is exactly what I want for a road trip.

A few small touches that make the map actually useful:

- Two base tile layers — Esri World Imagery for satellite (you can see which
  properties are next to a vineyard or olive grove) and CartoDB Positron for
  street-name navigation. A `LayerControl` lets me toggle them.
- A separate transparent overlay with road and city labels on top of the
  satellite imagery, so I can orient myself geographically without losing
  the photo view.
- `MarkerCluster` so dense areas like Otranto don't become unreadable when
  zoomed out.
- Marker color encodes rating (green ≥ 4.7, blue 4.4–4.6, orange below) and
  marker icon encodes source (leaf for Google only, home for agriturismo.it
  only, star for both). Color and icon are independent dimensions in
  `folium.Icon`, so a "green star" is a property both sources agree is
  excellent — the most trustworthy data point on the map.
- Popup HTML with name, rating, distance from the search center, address,
  clickable website + email + phone, license codes, source provenance, and
  the two newest reviews.

The final map for Salento has 109 unique places: 44 from Google only, 51
from agriturismo.it only, and 14 corroborated by both. The 14 starred
markers are the ones I trust most when picking where to actually book.

## What I'd do differently

1. **The agriturismo.it sitemap is the right entry point**, not the
   browseable region pages. I burned half an hour trying to scrape the
   `/it/agriturismi/puglia/lecce/` listing page before realizing it's only
   ~10 editorial cards and the real inventory lives in the sitemap.
2. **Field-level merge beats picking a winner.** My first attempt at dedup
   used a `score_row` heuristic to keep "the better" duplicate and threw
   the other away. That lost license codes from one row and reviews from
   the other. Combining is almost always cheaper than choosing.
3. **Don't dedupe too aggressively on coordinates alone.** I initially
   tried 300 m as a single threshold and got false positives. Two
   thresholds with name confirmation in the middle band is the right
   shape.

## Try it yourself

The code is on GitHub at <https://github.com/[your-username]/agriturismo-finder>.
You'll need a Google Places API key (free tier covers small searches). The
agriturismo.it scraper needs nothing.

```bash
git clone https://github.com/[your-username]/agriturismo-finder
cd agriturismo-finder
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp config.example.yaml config.yaml
# paste your API key into config.yaml

python main.py --location "Otranto, Italy" --radius 15
python scrape_agriturismo_it.py --province lecce
python merge.py "agriturismi_*.csv" --output merged.csv
python map.py merged.csv --output map.html
open map.html
```

Total time from clone to a usable map: about 90 seconds plus however long
the Google API takes to come back. Total cost for one search of one
location: a few cents in Places API charges, well within the monthly free
tier.

If you build something on top of this — different country, different kind
of accommodation, different data sources — I'd love to hear about it.
