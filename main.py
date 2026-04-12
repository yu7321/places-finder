#!/usr/bin/env python3
"""
Places Finder - Discover places near a location via the Google Places API.

The category (agriturismi, wineries, hotels, restaurants, etc.) is defined
by the `search_queries` and `require_keywords` sections in config.yaml.

Usage:
    python main.py --location "Chianti, Tuscany, Italy" --radius 30
    python main.py --location "Napa Valley, CA" --radius 20 --config wineries.yaml
"""

import argparse
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import yaml

from src.csv_writer import write_csv
from src.discovery import PlaceDiscovery, DiscoveryError
from src.email_scraper import EmailScraper
from src.models import Place


def slugify(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text[:40] or "search"


def build_output_path(template: str, location: str, radius: float) -> str:
    """Build a CSV path. If `template` already looks like a fixed filename
    (no `{}` placeholders), inject a `_<slug>_<timestamp>` suffix before the
    extension so each run produces a unique file."""
    slug = slugify(location)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    if "{" in template and "}" in template:
        return template.format(slug=slug, timestamp=timestamp, radius=int(radius))
    p = Path(template)
    stem = p.stem
    suffix = p.suffix or ".csv"
    return str(p.with_name(f"{stem}_{slug}_r{int(radius)}km_{timestamp}{suffix}"))


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}
    env_key = os.environ.get("GOOGLE_API_KEY")
    if env_key:
        config["google_api_key"] = env_key
    if not config.get("google_api_key"):
        raise ValueError("google_api_key missing in config and GOOGLE_API_KEY not set")
    return config


def main() -> None:
    parser = argparse.ArgumentParser(description="Find places near a location.")
    parser.add_argument("--config", default="config.yaml", help="Path to config file")
    parser.add_argument("--location", help="Location to search around (address or place name)")
    parser.add_argument("--radius", type=float, help="Search radius in km")
    parser.add_argument("--output", help="Output CSV path (overrides config)")
    parser.add_argument(
        "--skip-emails",
        action="store_true",
        help="Skip scraping websites for contact emails",
    )
    args = parser.parse_args()

    try:
        config = load_config(args.config)
    except Exception as e:
        print(f"Error loading config: {e}")
        sys.exit(1)

    profile = config.get("profile") or {}
    profile_name = profile.get("name", "place")
    profile_label = profile.get("label", "places")

    location = args.location or config.get("default_location")
    radius = args.radius or config.get("default_radius_km", 30)
    if not location:
        print("Error: no --location given and no default_location in config")
        sys.exit(1)

    output_cfg = config.get("output", {})
    default_csv = output_cfg.get("csv_path") or f"{profile_name}.csv"
    csv_template = args.output or default_csv
    csv_path = (
        args.output if args.output and "{" not in args.output
        else build_output_path(csv_template, location, radius)
    )
    encoding = output_cfg.get("encoding", "utf-8")
    max_reviews = int(output_cfg.get("max_reviews_per_place", 3))

    print(f"Profile:  {profile_label}")
    print(f"Location: {location}")
    print(f"Radius:   {radius} km")
    print(f"Output:   {csv_path}")
    print()

    discovery = PlaceDiscovery(config, location=location, radius_km=radius)

    try:
        results: list[Place] = []
        for a in discovery.discover():
            results.append(a)
            print(
                f"  + {a.name} ({a.distance_km} km)"
                f"{' ★' + format(a.rating, '.1f') if a.rating else ''}"
            )
    except DiscoveryError as e:
        print(f"Discovery failed: {e}")
        sys.exit(1)

    if not results:
        print(f"\nNo {profile_label} found.")
        sys.exit(0)

    if not args.skip_emails:
        scrapeable = [r for r in results if r.website]
        print(f"\nScraping {len(scrapeable)} websites for contact emails...")
        scraper = EmailScraper()

        def progress(i: int, total: int, name: str) -> None:
            print(f"  [{i}/{total}] {name}")

        scraper.enrich(scrapeable, progress_callback=progress)
        with_email = sum(1 for r in results if r.email)
        print(f"Found emails for {with_email}/{len(scrapeable)} sites.")

    count = write_csv(csv_path, results, encoding=encoding, max_reviews=max_reviews)
    print(f"\nWrote {count} {profile_label} to {csv_path}")
    print("Import into Google Sheets via File → Import → Upload.")


if __name__ == "__main__":
    main()
