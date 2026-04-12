#!/usr/bin/env python3
"""
Scrape agriturismo.it for agriturismi in a region/province.

Uses the canonical Italian site (www.agriturismo.it), which serves Italian
region and province slugs in its URLs (e.g. puglia/lecce, toscana/siena).

Usage:
    python scrape_agriturismo_it.py
    python scrape_agriturismo_it.py --region puglia --province lecce
    python scrape_agriturismo_it.py --region toscana --province siena
    python scrape_agriturismo_it.py --center 40.27,17.92 --radius 15
    python scrape_agriturismo_it.py --province lecce --center 40.27,17.92 --radius 15

Outputs a CSV with the same schema as `main.py` so the result drops straight
into `merge.py` and `map.py`.
"""

import argparse
import sys
from datetime import datetime

from src.agriturismo_it import scrape, AgriturismoItError
from src.csv_writer import write_csv
from src.email_scraper import EmailScraper
from src.models import Place, SOURCE_AGRITURISMO_IT


def parse_center(s: str) -> tuple[float, float]:
    try:
        lat_s, lng_s = s.split(",")
        return float(lat_s.strip()), float(lng_s.strip())
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"--center must be 'lat,lng', got {s!r}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape agriturismo.it")
    parser.add_argument("--region", default="puglia",
                        help="Italian region slug (default: puglia)")
    parser.add_argument("--province", default="lecce",
                        help="Italian province slug, or empty string for whole region")
    parser.add_argument("--center", type=parse_center,
                        help="Optional 'lat,lng' for distance + radius filter")
    parser.add_argument("--radius", type=float,
                        help="Radius in km from --center (requires --center)")
    parser.add_argument("--delay", type=float, default=0.7,
                        help="Seconds between detail-page fetches")
    parser.add_argument("--skip-emails", action="store_true",
                        help="Don't run the email scraper on the result rows")
    parser.add_argument("--output", help="Output CSV path")
    args = parser.parse_args()

    if args.radius is not None and args.center is None:
        print("Error: --radius requires --center")
        sys.exit(2)

    province = args.province or None

    try:
        rows: list[Place] = list(
            scrape(
                region=args.region,
                province=province,
                center=args.center,
                radius_km=args.radius,
                delay=args.delay,
            )
        )
    except AgriturismoItError as e:
        print(f"Error: {e}")
        sys.exit(1)

    if not rows:
        print("\nNo rows scraped.")
        sys.exit(0)

    print(f"\nParsed {len(rows)} place(s).")

    # Detail pages don't expose owner email; the `website` field holds the
    # agriturismo.it listing URL itself, so the email scraper would only find
    # agriturismo.it's own addresses. Skip unless an external owner site shows up.
    if not args.skip_emails:
        external = [r for r in rows if r.website and SOURCE_AGRITURISMO_IT not in r.website]
        if external:
            print(f"Scraping {len(external)} external website(s) for emails...")
            EmailScraper().enrich(external)

    if args.output:
        out = args.output
    else:
        slug = args.province or args.region
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        out = f"agriturismi_it_{slug}_{ts}.csv"

    count = write_csv(out, rows)
    print(f"Wrote {count} rows to {out}")


if __name__ == "__main__":
    main()
