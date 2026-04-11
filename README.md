# Places Finder

A configurable pipeline for finding, enriching, deduplicating, and mapping
places via the Google Places API (New). It was built to plan a
farm-stay trip along the Salento coast, and the shipped example config is
tuned for *agriturismi*, but the pipeline itself is category-agnostic —
point it at wineries in Napa, specialty coffee in Berlin, refugi in the
Pyrenees, or anything else Google Places indexes.

**[Live demo map →](https://yu7321.github.io/places-finder/demo.html)** ·
[Build story](./BLOG_POST.md) ·
[Bundled example configs](./examples/)

## What it does

1. **Discover** places around a location via multiple Google Places text
   searches (more queries → better recall than a single search), then
   filter by a keyword allowlist so generic hotels don't pollute the
   results.
2. **Enrich** each place by following its website and scraping the
   contact/impressum page for an owner email address.
3. **(Optional) Scrape a domain-specific source.** For the agriturismo
   use case, there's a bundled scraper for `agriturismo.it` (HomeToGo
   metasearch, canonical Italian site) that walks the public sitemap and
   extracts direct owner phone numbers and Italian tourism license codes
   from the embedded JSON.
4. **Merge** multiple CSVs from different sources with two-tier
   coordinate-based deduplication: within 50 m always merges; 50–250 m
   merges only when names share a significant token. Field values are
   combined best-of-both rather than discarded.
5. **Render** the result as a self-contained interactive HTML map with
   satellite + street tile layers, marker clustering, rating-based color,
   source-distinguishing icons, and rich popups. No API key, no server,
   no JS build step — just open the HTML file.

## Features

- Multi-query Google Places search with keyword filtering
- Language-agnostic email scraper (follows typical contact/impressum paths
  in Italian, English, German, Spanish)
- Domain-specific sitemap scraper for agriturismo.it as an opt-in second
  source
- Two-tier coordinate + name dedup that collapses cross-source duplicates
  without merging genuinely different neighboring places
- Folium-based HTML map with rating-by-color and source-by-icon encoding
- Fully-configurable profile: change queries, keywords, language code,
  and label in `config.yaml` to retarget at a different category

## Requirements

- Python 3.10+
- A Google Cloud project with the **Places API (New)** enabled and an API
  key. See
  [Places API pricing](https://developers.google.com/maps/documentation/places/web-service/usage-and-billing) —
  the free tier covers small searches.

## Setup

```bash
git clone https://github.com/<you>/places-finder.git
cd places-finder

python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp config.example.yaml config.yaml
# Edit config.yaml and paste your Google Places API key, or set the
# GOOGLE_API_KEY env var (which overrides the file value).
```

`config.yaml` is gitignored.

## Usage

### Discover places

```bash
python main.py --location "Punta Prosciutto, 73010 Lecce, Italy" --radius 11
```

Writes a timestamped CSV to the current directory, sorted by rating then
distance. Useful flags:

- `--config` — use a different config file (defaults to `config.yaml`)
- `--location` — any text Google understands (city, address, landmark)
- `--radius` — search radius in km
- `--skip-emails` — skip the email scraping pass
- `--output` — explicit output path

### Retarget at a different category

The category is entirely config-driven. Try one of the bundled examples:

```bash
python main.py --config examples/wineries_napa.yaml
python main.py --config examples/specialty_coffee_berlin.yaml
```

Or write your own by copying `config.example.yaml` and editing three
sections:

```yaml
profile:
  name: "refugi"                # filename stem and log label
  label: "Pyrenean mountain huts"

search_queries:                 # multiple phrasings → better recall
  - "mountain hut"
  - "refugi de muntanya"
  - "refugio de montaña"

require_keywords:               # name/type allowlist to filter noise
  - "refugi"
  - "refugio"
  - "hut"
```

That's it. No code changes needed.

### (Optional) Scrape agriturismo.it

Only relevant for the agriturismo use case. Hits the canonical Italian
site (`www.agriturismo.it`) and accepts Italian region/province slugs:

```bash
python scrape_agriturismo_it.py --region puglia --province lecce
python scrape_agriturismo_it.py --region toscana --province siena
```

Walks the public XML sitemap, filters detail URLs by region/province,
fetches each detail page, and extracts the embedded JSON. Outputs a CSV
with the same schema as `main.py` so it merges cleanly. Optional
`--center lat,lng --radius km` filters the result to a radius from a
point.

### Merge multiple CSVs

```bash
python merge.py "agriturismi_*.csv" agriturismi_it_lecce_*.csv \
    --output merged.csv
```

Two-pass dedup: first by Google `place_id`, then by coordinate proximity
with name-token confirmation. Fields from both sources are combined —
direct owner phone from agriturismo.it, reviews from Google, license
codes wherever they exist.

### Render the map

```bash
python map.py merged.csv --output map.html
open map.html
```

The HTML file is fully self-contained. Host it on GitHub Pages, drop it
on a USB stick, or open it offline.

Marker color encodes rating (green ≥ 4.7, blue 4.4–4.6, orange below).
Marker icon encodes source: leaf for Google-only, home for agriturismo.it
only, star for places found in both.

## Project layout

```
.
├── main.py                     # Google Places discovery CLI (generic)
├── scrape_agriturismo_it.py    # agriturismo.it sitemap scraper CLI (domain-specific)
├── merge.py                    # Multi-source dedup + merge
├── map.py                      # Folium-based HTML map renderer
├── config.example.yaml         # Config template (agriturismo example)
├── examples/
│   ├── wineries_napa.yaml      # Example retarget: wineries
│   └── specialty_coffee_berlin.yaml
├── requirements.txt
└── src/
    ├── models.py               # Place + Review dataclasses, CSV schema
    ├── discovery.py            # Google Places API client (generic)
    ├── email_scraper.py        # Website email scraping
    ├── csv_writer.py           # CSV output
    └── agriturismo_it.py       # HomeToGo / agriturismo.it parser (domain-specific)
```

Modules labeled "generic" work for any category; modules labeled
"domain-specific" are the optional agriturismo source. Strip them out if
you're repurposing the project.

## Data and privacy

Output CSVs and HTML maps contain phone numbers, addresses, ratings, and
review excerpts of real businesses. They are gitignored by default. If
you publish them, treat them as third-party data and respect the ToS of
the upstream sources (Google Places, any site you scrape).

The agriturismo.it scraper hits the publicly served sitemap and HTML
detail pages with a polite default delay of 0.7 s between requests and
identifies itself with a normal browser User-Agent. None of the paths
disallowed by the site's `robots.txt` are touched.

## License

MIT — see `LICENSE`.

## Acknowledgements

- [Google Places API (New)](https://developers.google.com/maps/documentation/places/web-service/overview)
- [HomeToGo / agriturismo.it](https://www.agriturismo.it/) for serving rich
  structured data on their public detail pages
- [folium](https://python-visualization.github.io/folium/) and
  [Leaflet.js](https://leafletjs.com/) for the map rendering
- [Esri World Imagery](https://www.arcgis.com/home/item.html?id=10df2279f9684e4a9f6a7f08febac2a9)
  for the satellite tiles
