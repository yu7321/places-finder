#!/usr/bin/env python3
"""
Merge multiple agriturismi CSV files into one, deduplicated by place_id and
then by coordinate proximity (with name-overlap as a tiebreaker).

Usage:
    python merge.py file1.csv file2.csv [...] [--output merged.csv]
    python merge.py "agriturismi_*.csv" --output merged.csv
"""

import argparse
import csv
import glob
import math
import re
import sys
from datetime import datetime
from pathlib import Path

from src.models import CSV_COLUMNS


# Distance thresholds for the coordinate-based dedup pass.
CLOSE_M = 50      # below this, merge regardless of name
FUZZY_M = 250     # below this, merge only if names share a significant token

NAME_STOPWORDS = {
    "agriturismo", "agriturismi", "masseria", "tenuta", "podere",
    "azienda", "agricola", "bio", "biologico", "ristorante", "country",
    "hotel", "camping", "resort", "the", "and", "con", "del", "della",
    "delle", "dei", "il", "la", "le", "lo", "di", "da", "in",
}


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6_371_000.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def name_tokens(name: str) -> set[str]:
    raw = re.findall(r"[a-zA-Z0-9]+", (name or "").lower())
    return {t for t in raw if len(t) > 3 and t not in NAME_STOPWORDS}


def names_overlap(a: str, b: str) -> bool:
    return bool(name_tokens(a) & name_tokens(b))


def _row_lat_lng(row: dict) -> tuple[float, float] | None:
    try:
        lat = float(row.get("latitude") or 0)
        lng = float(row.get("longitude") or 0)
    except ValueError:
        return None
    if lat == 0 and lng == 0:
        return None
    return lat, lng


def _row_sources(row: dict) -> set[str]:
    raw = row.get("source") or "google_places"
    return {s.strip() for s in raw.split(";") if s.strip()}


def _is_real_website(w: str) -> bool:
    return bool(w) and "agriturismo.it" not in w


def merge_pair(a: dict, b: dict) -> dict:
    """Combine two rows that represent the same physical place. Picks the
    best value field-by-field rather than dropping one row's data."""
    out = dict(a)

    # Longest descriptive name wins.
    out["name"] = max((a.get("name") or "", b.get("name") or ""), key=len)

    # Phone: prefer the row sourced from agriturismo.it (direct owner phone),
    # fall back to whichever is non-empty.
    a_src, b_src = _row_sources(a), _row_sources(b)
    a_phone, b_phone = (a.get("phone") or "").strip(), (b.get("phone") or "").strip()
    if "agriturismo.it" in a_src and a_phone:
        out["phone"] = a_phone
    elif "agriturismo.it" in b_src and b_phone:
        out["phone"] = b_phone
    else:
        out["phone"] = a_phone or b_phone

    # Website: prefer a real owner site over the agriturismo.it metasearch link.
    a_web, b_web = (a.get("website") or "").strip(), (b.get("website") or "").strip()
    if _is_real_website(a_web):
        out["website"] = a_web
    elif _is_real_website(b_web):
        out["website"] = b_web
    else:
        out["website"] = a_web or b_web

    # Simple non-empty wins (a takes precedence).
    for f in ("email", "google_maps_url", "license_codes", "reviews"):
        out[f] = (a.get(f) or "") or (b.get(f) or "")

    # Address: longer wins.
    out["address"] = max(
        (a.get("address") or "", b.get("address") or ""), key=len
    )

    # Rating: prefer the row with more reviews; user_rating_count = max.
    try:
        a_count = int(a.get("user_rating_count") or 0)
    except (TypeError, ValueError):
        a_count = 0
    try:
        b_count = int(b.get("user_rating_count") or 0)
    except (TypeError, ValueError):
        b_count = 0
    if b_count > a_count:
        out["rating"] = b.get("rating") or ""
    else:
        out["rating"] = a.get("rating") or ""
    out["user_rating_count"] = str(max(a_count, b_count)) if max(a_count, b_count) else ""

    # Combined source list, sorted for stability.
    out["source"] = "; ".join(sorted(a_src | b_src))

    # Combined place_id list (pipe-separated), so future merges still match.
    pids: set[str] = set()
    for r in (a, b):
        for p in (r.get("place_id") or "").split("|"):
            p = p.strip()
            if p:
                pids.add(p)
    out["place_id"] = "|".join(sorted(pids))

    # File-provenance set (used to populate the `sources` column).
    out["_files"] = a.get("_files", set()) | b.get("_files", set())

    return out


def coord_dedup(rows: list[dict]) -> tuple[list[dict], int]:
    """Cluster rows whose coords are within CLOSE_M, or within FUZZY_M and
    sharing a name token. Returns (deduped_rows, merge_count)."""
    out: list[dict] = []
    merges = 0
    for row in rows:
        coords = _row_lat_lng(row)
        if coords is None:
            out.append(row)
            continue
        lat, lng = coords

        absorbed = False
        for i, existing in enumerate(out):
            ex_coords = _row_lat_lng(existing)
            if ex_coords is None:
                continue
            d = haversine_m(lat, lng, ex_coords[0], ex_coords[1])
            if d > FUZZY_M:
                continue
            if d <= CLOSE_M or names_overlap(row.get("name", ""), existing.get("name", "")):
                out[i] = merge_pair(existing, row)
                absorbed = True
                merges += 1
                break
        if not absorbed:
            out.append(row)
    return out, merges


def merge_csvs(paths: list[str], output: str) -> tuple[int, int, int]:
    all_rows: list[dict] = []
    for path in paths:
        src = Path(path).name
        with open(path, "r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                row["_files"] = {src}
                if not row.get("source"):
                    row["source"] = "google_places"
                all_rows.append(row)
    total = len(all_rows)

    by_id: dict[str, dict] = {}
    for row in all_rows:
        pid = row.get("place_id") or f"noid:{row.get('name','')}|{row.get('address','')}"
        existing = by_id.get(pid)
        if existing is None:
            by_id[pid] = row
        else:
            by_id[pid] = merge_pair(existing, row)

    deduped, coord_merges = coord_dedup(list(by_id.values()))

    for row in deduped:
        row["sources"] = "; ".join(sorted(row.pop("_files", set())))

    deduped.sort(
        key=lambda r: (
            -float(r.get("rating") or 0),
            float(r.get("distance_km") or 0),
            (r.get("name") or "").lower(),
        )
    )

    columns = CSV_COLUMNS + ["sources"]
    with open(output, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in deduped:
            writer.writerow(row)

    return total, len(deduped), coord_merges


def main() -> None:
    parser = argparse.ArgumentParser(description="Merge agriturismi CSV files.")
    parser.add_argument("inputs", nargs="+", help="CSV file paths or glob patterns")
    parser.add_argument(
        "--output",
        help="Output CSV path (default: merged_<timestamp>.csv)",
    )
    args = parser.parse_args()

    paths: list[str] = []
    for pattern in args.inputs:
        matched = sorted(glob.glob(pattern))
        if matched:
            paths.extend(matched)
        elif Path(pattern).exists():
            paths.append(pattern)
        else:
            print(f"Warning: no files matched '{pattern}'")

    if not paths:
        print("Error: no input files found")
        sys.exit(1)

    output = args.output or f"merged_{datetime.now().strftime('%Y%m%d-%H%M%S')}.csv"

    print(f"Merging {len(paths)} file(s):")
    for p in paths:
        print(f"  - {p}")

    total, unique, coord_merges = merge_csvs(paths, output)
    place_id_unique = unique + coord_merges
    print(f"\nRead {total} rows; place_id pass kept {place_id_unique} unique;")
    print(f"coordinate dedup collapsed {coord_merges} additional duplicates.")
    print(f"Final unique places: {unique}")
    print(f"Wrote {output}")


if __name__ == "__main__":
    main()
