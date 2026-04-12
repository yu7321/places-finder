"""Google Places API (New) discovery.

Category-agnostic. The category is defined by `search_queries` and
`require_keywords` in the config (see `config.example.yaml`).
"""

import time
from typing import Any, Generator

import requests

from .geo import haversine_km
from .models import Place, Review


class DiscoveryError(Exception):
    pass


class PlaceDiscovery:
    TEXT_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"

    PLACE_FIELDS = [
        "places.id",
        "places.displayName",
        "places.formattedAddress",
        "places.location",
        "places.websiteUri",
        "places.nationalPhoneNumber",
        "places.internationalPhoneNumber",
        "places.googleMapsUri",
        "places.primaryType",
        "places.types",
        "places.rating",
        "places.userRatingCount",
        "places.reviews",
    ]

    def __init__(self, config: dict[str, Any], location: str, radius_km: float):
        self.api_key = config["google_api_key"]
        self.location = location
        self.radius_km = float(radius_km)
        self.radius_m = int(self.radius_km * 1000)
        self.search_queries = config.get("search_queries") or []
        self.require_keywords = [
            k.lower() for k in (config.get("require_keywords") or [])
        ]
        self.language_code = config.get("language_code", "en")
        if not self.search_queries:
            raise DiscoveryError(
                "config.search_queries is empty — nothing to search for"
            )
        self.base_lat: float | None = None
        self.base_lng: float | None = None

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": self.api_key,
            "X-Goog-FieldMask": ",".join(self.PLACE_FIELDS),
        }

    def geocode(self) -> None:
        """Resolve the location string to coordinates using Places API text
        search. Avoids requiring the separate Geocoding API to be enabled."""
        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": self.api_key,
            "X-Goog-FieldMask": "places.location,places.formattedAddress,places.displayName",
        }
        body = {"textQuery": self.location, "maxResultCount": 1}
        try:
            resp = requests.post(
                self.TEXT_SEARCH_URL, json=body, headers=headers, timeout=15
            )
            resp.raise_for_status()
        except requests.exceptions.HTTPError as e:
            msg = str(e)
            if e.response is not None:
                try:
                    msg = e.response.json().get("error", {}).get("message", msg)
                except Exception:
                    pass
            raise DiscoveryError(f"Failed to geocode '{self.location}': {msg}")

        places = resp.json().get("places", [])
        if not places:
            raise DiscoveryError(f"No place found for '{self.location}'")
        loc = places[0].get("location") or {}
        self.base_lat = loc.get("latitude")
        self.base_lng = loc.get("longitude")
        if self.base_lat is None or self.base_lng is None:
            raise DiscoveryError(f"Place for '{self.location}' has no coordinates")
        resolved = (
            (places[0].get("formattedAddress") or "")
            or (places[0].get("displayName") or {}).get("text", "")
        )
        print(
            f"Resolved '{self.location}' -> {self.base_lat:.4f}, {self.base_lng:.4f}"
            f" ({resolved})"
        )

    def _distance_km(self, lat: float, lng: float) -> float:
        return round(haversine_km(self.base_lat, self.base_lng, lat, lng), 2)

    def _matches_keyword(self, name: str, types: list[str]) -> bool:
        if not self.require_keywords:
            return True
        haystack = (name or "").lower() + " " + " ".join(types or []).lower()
        return any(k in haystack for k in self.require_keywords)

    def _parse_reviews(self, raw_reviews: list[dict[str, Any]]) -> list[Review]:
        out: list[Review] = []
        for r in raw_reviews or []:
            text_obj = r.get("text") or r.get("originalText") or {}
            text = text_obj.get("text", "") if isinstance(text_obj, dict) else str(text_obj)
            author_obj = r.get("authorAttribution") or {}
            author = author_obj.get("displayName", "") if isinstance(author_obj, dict) else ""
            out.append(
                Review(
                    author=author,
                    rating=float(r.get("rating") or 0),
                    text=text,
                    published=r.get("publishTime", ""),
                )
            )
        # Google returns up to 5 reviews ordered by relevance; re-sort by
        # publish time descending so the newest appear first.
        out.sort(key=lambda r: r.published or "", reverse=True)
        return out

    def _parse_place(self, place: dict[str, Any]) -> Place | None:
        display = place.get("displayName") or {}
        name = display.get("text", "") if isinstance(display, dict) else str(display)
        types = place.get("types") or []
        primary = place.get("primaryType") or ""
        if primary:
            types = list(types) + [primary]

        if not name or not self._matches_keyword(name, types):
            return None

        loc = place.get("location") or {}
        lat = loc.get("latitude", 0.0)
        lng = loc.get("longitude", 0.0)
        distance = self._distance_km(lat, lng)
        if distance > self.radius_km:
            return None

        return Place(
            place_id=place.get("id", ""),
            name=name,
            website=place.get("websiteUri", "") or "",
            google_maps_url=place.get("googleMapsUri", "") or "",
            address=place.get("formattedAddress", "") or "",
            phone=(
                place.get("internationalPhoneNumber")
                or place.get("nationalPhoneNumber")
                or ""
            ),
            latitude=float(lat),
            longitude=float(lng),
            distance_km=distance,
            rating=float(place.get("rating") or 0),
            user_rating_count=int(place.get("userRatingCount") or 0),
            reviews=self._parse_reviews(place.get("reviews", [])),
        )

    def _search_text(self, query: str) -> Generator[dict[str, Any], None, None]:
        body = {
            "textQuery": query,
            "locationBias": {
                "circle": {
                    "center": {"latitude": self.base_lat, "longitude": self.base_lng},
                    "radius": float(self.radius_m),
                }
            },
            "maxResultCount": 20,
            "languageCode": self.language_code,
        }
        try:
            resp = requests.post(
                self.TEXT_SEARCH_URL, json=body, headers=self._headers(), timeout=15
            )
            resp.raise_for_status()
            for place in resp.json().get("places", []):
                yield place
        except requests.exceptions.HTTPError as e:
            msg = str(e)
            if e.response is not None:
                try:
                    msg = e.response.json().get("error", {}).get("message", msg)
                except Exception:
                    pass
            print(f"  ! text search failed for '{query}': {msg}")
        except Exception as e:
            print(f"  ! text search failed for '{query}': {e}")

    def discover(self) -> Generator[Place, None, None]:
        if self.base_lat is None or self.base_lng is None:
            self.geocode()

        seen: set[str] = set()
        for query in self.search_queries:
            print(f"Searching: {query!r}")
            count_before = len(seen)
            for place in self._search_text(query):
                pid = place.get("id", "")
                if not pid or pid in seen:
                    continue
                seen.add(pid)
                parsed = self._parse_place(place)
                if parsed:
                    yield parsed
            print(f"  + {len(seen) - count_before} new place ids")
            time.sleep(0.2)

        print(f"Discovery complete. {len(seen)} unique place ids seen.")
