"""Scraper for www.agriturismo.it.

Walks the site's public XML sitemap, picks detail pages by region/province
slug, fetches each page, and extracts two JSON islands embedded in the HTML
(`rentalOfferDetails` and `static-data-json`). Returns Place objects with
source="agriturismo.it". No external API; no headless browser.

The site uses Italian region/province slugs in URLs, e.g.
`/it/agriturismi/puglia/lecce/<Slug>-<id>/index.html`. Pass the Italian
slugs to `scrape(region=..., province=...)`.
"""

import json
import re
import time
from typing import Generator
from urllib.parse import urlparse

import requests

from .geo import haversine_km
from .models import Place, SOURCE_AGRITURISMO_IT


SITEMAP_INDEX = "https://www.agriturismo.it/sitemap.xml"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0 Safari/537.36"
)

RTK_RE = re.compile(
    r'<script type="application/json" data-rtk-endpoint="rentalOfferDetails"'
    r'[^>]*>(.*?)</script>',
    re.DOTALL,
)
STATIC_RE = re.compile(
    r'<script type="application/json" id="static-data-json">(.*?)</script>',
    re.DOTALL,
)
LOC_RE = re.compile(r"<loc>([^<]+)</loc>")


class AgriturismoItError(Exception):
    pass


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept-Language": "it-IT,it;q=0.9,en;q=0.8",
            "Accept": "text/html,application/xhtml+xml",
        }
    )
    return s


def fetch_detail_urls(session: requests.Session) -> list[str]:
    """Fetch sitemap index, then every detail sitemap, return all detail URLs."""
    try:
        resp = session.get(SITEMAP_INDEX, timeout=20)
        resp.raise_for_status()
        sitemaps = [
            u for u in LOC_RE.findall(resp.text) if "/sitemap/details/" in u
        ]
        if not sitemaps:
            raise AgriturismoItError(
                f"sitemap index at {SITEMAP_INDEX} contained no detail sitemaps"
            )
        urls: list[str] = []
        for sm in sitemaps:
            r = session.get(sm, timeout=20)
            r.raise_for_status()
            urls.extend(LOC_RE.findall(r.text))
    except requests.RequestException as e:
        raise AgriturismoItError(f"failed to fetch sitemap: {e}") from e
    return urls


def filter_by_path(urls: list[str], region: str, province: str | None) -> list[str]:
    """Pick URLs whose path contains /<region>/[<province>/]."""
    needle = f"/{region}/"
    if province:
        needle = f"/{region}/{province}/"
    return [u for u in urls if needle in urlparse(u).path]


def parse_detail(html: str, url: str) -> Place | None:
    """Pull objectTitle / geoLocation / ownerInfo / ratings / propertyAddress
    out of the embedded JSON. Returns None if the rtk payload is missing."""
    rtk_m = RTK_RE.search(html)
    if not rtk_m:
        return None
    try:
        rtk = json.loads(rtk_m.group(1))
    except json.JSONDecodeError:
        return None

    static = {}
    static_m = STATIC_RE.search(html)
    if static_m:
        try:
            static = json.loads(static_m.group(1)).get("data", {}) or {}
        except json.JSONDecodeError:
            pass

    name = (rtk.get("objectTitle") or "").strip()
    if not name:
        return None

    geo = rtk.get("geoLocation") or {}
    lat = float(geo.get("lat") or 0)
    lng = float(geo.get("lon") or 0)
    if lat == 0 and lng == 0:
        return None

    pa = static.get("propertyAddress") or {}
    line = (pa.get("address") or "").strip()
    rest = (pa.get("content") or "").strip()
    address = ", ".join(p for p in (line, rest) if p)

    owner = rtk.get("ownerInfo") or {}
    phone = (owner.get("phone") or "").strip()

    ratings = rtk.get("ratings") or {}
    star = (ratings.get("starValue") or "0").replace(",", ".")
    try:
        rating = float(star)
    except ValueError:
        rating = 0.0
    try:
        review_count = int(ratings.get("reviewCount") or 0)
    except (TypeError, ValueError):
        review_count = 0

    licenses: list[str] = []
    compliance = (owner.get("complianceData") or {}).get("list") or []
    for item in compliance:
        label = (item.get("label") or "").strip()
        if label:
            licenses.append(label)
    license_codes = " | ".join(licenses)

    place_id = f"{SOURCE_AGRITURISMO_IT}:{rtk.get('rentalObjectId') or static.get('objectId') or url}"

    return Place(
        place_id=place_id,
        name=name,
        website=url,
        google_maps_url="",
        address=address,
        phone=phone,
        latitude=lat,
        longitude=lng,
        distance_km=0.0,
        rating=rating,
        user_rating_count=review_count,
        reviews=[],
        source=SOURCE_AGRITURISMO_IT,
        license_codes=license_codes,
    )


def scrape(
    region: str = "puglia",
    province: str | None = "lecce",
    center: tuple[float, float] | None = None,
    radius_km: float | None = None,
    delay: float = 0.7,
) -> Generator[Place, None, None]:
    """Iterate matching detail pages and yield parsed Place rows.

    If `center` and `radius_km` are given, distances are computed and rows
    outside the radius are dropped.
    """
    session = _session()
    print(f"Fetching sitemap from {SITEMAP_INDEX}")
    all_urls = fetch_detail_urls(session)
    print(f"  total detail URLs: {len(all_urls)}")

    matched = filter_by_path(all_urls, region, province)
    print(
        f"  filtered to region={region!r}"
        + (f" province={province!r}" if province else "")
        + f": {len(matched)} URLs"
    )

    for i, url in enumerate(matched, 1):
        try:
            r = session.get(url, timeout=20)
            r.raise_for_status()
        except requests.RequestException as e:
            print(f"  ! [{i}/{len(matched)}] fetch failed: {url} ({e})")
            time.sleep(delay)
            continue

        a = parse_detail(r.text, url)
        if a is None:
            print(f"  ? [{i}/{len(matched)}] no rtk payload: {url}")
            time.sleep(delay)
            continue

        if center is not None:
            a.distance_km = round(
                haversine_km(center[0], center[1], a.latitude, a.longitude), 2
            )
            if radius_km is not None and a.distance_km > radius_km:
                time.sleep(delay)
                continue

        print(f"  + [{i}/{len(matched)}] {a.name}  ({a.latitude:.4f},{a.longitude:.4f})")
        yield a
        time.sleep(delay)
