"""Data models for the places pipeline."""

from dataclasses import dataclass, field
from typing import List


@dataclass
class Review:
    author: str = ""
    rating: float = 0.0
    text: str = ""
    published: str = ""

    def to_compact(self) -> str:
        rating_str = f"{self.rating:.0f}/5" if self.rating else ""
        author = self.author or "Anonymous"
        date = (self.published or "")[:10]
        text = (self.text or "").replace("\n", " ").strip()
        if len(text) > 280:
            text = text[:277] + "..."
        header_parts = [p for p in [date, author, rating_str] if p]
        header = " · ".join(header_parts)
        return f"{header}\n{text}" if text else header


@dataclass
class Place:
    """A single result from any supported source (Google Places, a domain
    scraper, etc.). The schema is deliberately category-agnostic — the same
    fields work for agriturismi, wineries, restaurants, hotels, or anything
    else the Places API returns."""

    place_id: str
    name: str
    website: str = ""
    google_maps_url: str = ""
    address: str = ""
    phone: str = ""
    email: str = ""
    latitude: float = 0.0
    longitude: float = 0.0
    distance_km: float = 0.0
    rating: float = 0.0
    user_rating_count: int = 0
    reviews: List[Review] = field(default_factory=list)
    source: str = "google_places"
    license_codes: str = ""

    def reviews_joined(self, max_reviews: int = 5) -> str:
        if not self.reviews:
            return ""
        return "\n\n".join(r.to_compact() for r in self.reviews[:max_reviews])


CSV_COLUMNS = [
    "name",
    "website",
    "email",
    "phone",
    "google_maps_url",
    "address",
    "latitude",
    "longitude",
    "distance_km",
    "rating",
    "user_rating_count",
    "reviews",
    "place_id",
    "source",
    "license_codes",
]
