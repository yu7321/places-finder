"""CSV output for places."""

import csv
from typing import Iterable

from .models import Place, CSV_COLUMNS


def write_csv(
    path: str,
    rows: Iterable[Place],
    encoding: str = "utf-8",
    max_reviews: int = 3,
) -> int:
    sorted_rows = sorted(
        rows,
        key=lambda a: (-a.rating, a.distance_km, a.name.lower()),
    )
    with open(path, "w", newline="", encoding=encoding) as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for a in sorted_rows:
            writer.writerow(
                {
                    "name": a.name,
                    "website": a.website,
                    "email": a.email,
                    "phone": a.phone,
                    "google_maps_url": a.google_maps_url,
                    "address": a.address,
                    "latitude": f"{a.latitude:.6f}" if a.latitude else "",
                    "longitude": f"{a.longitude:.6f}" if a.longitude else "",
                    "distance_km": a.distance_km,
                    "rating": a.rating or "",
                    "user_rating_count": a.user_rating_count or "",
                    "reviews": a.reviews_joined(max_reviews),
                    "place_id": a.place_id,
                    "source": a.source,
                    "license_codes": a.license_codes,
                }
            )
    return len(sorted_rows)
