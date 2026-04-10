#!/usr/bin/env python3
"""
Render agriturismi CSVs as a self-contained interactive HTML map.

Usage:
    python map.py merged_20260410-071233.csv
    python map.py "agriturismi_*.csv"
    python map.py file1.csv file2.csv --output map.html

Open the resulting HTML in any browser. No API key needed at view time.
"""

import argparse
import csv
import glob
import html
import sys
from datetime import datetime
from pathlib import Path

try:
    import folium
    from folium.plugins import MarkerCluster
except ImportError:
    print("Error: folium is not installed. Run: pip install -r requirements.txt")
    sys.exit(1)


def collect_rows(paths: list[str]) -> tuple[list[dict], int]:
    """Read all CSVs, return rows that have valid lat/lng plus a skip count."""
    rows: list[dict] = []
    skipped = 0
    seen_ids: set[str] = set()

    for path in paths:
        with open(path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                lat_s = (row.get("latitude") or "").strip()
                lng_s = (row.get("longitude") or "").strip()
                try:
                    lat = float(lat_s)
                    lng = float(lng_s)
                except ValueError:
                    skipped += 1
                    continue
                if lat == 0.0 and lng == 0.0:
                    skipped += 1
                    continue

                pid = row.get("place_id") or f"{row.get('name','')}|{lat},{lng}"
                if pid in seen_ids:
                    continue
                seen_ids.add(pid)

                row["_lat"] = lat
                row["_lng"] = lng
                rows.append(row)

    return rows, skipped


def color_for_rating(rating_str: str) -> str:
    try:
        r = float(rating_str or 0)
    except ValueError:
        r = 0.0
    if r >= 4.7:
        return "green"
    if r >= 4.4:
        return "blue"
    return "orange"


def icon_for_source(source: str) -> str:
    """leaf = Google Places only, home = agriturismo.it only, star = both."""
    parts = {s.strip() for s in (source or "google_places").split(";") if s.strip()}
    has_g = "google_places" in parts
    has_a = "agriturismo.it" in parts
    if has_g and has_a:
        return "star"
    if has_a:
        return "home"
    return "leaf"


def truncate(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def build_popup_html(row: dict) -> str:
    name = html.escape(row.get("name") or "Unknown")
    rating = row.get("rating") or ""
    count = row.get("user_rating_count") or ""
    distance = row.get("distance_km") or ""
    address = html.escape(row.get("address") or "")
    website = (row.get("website") or "").strip()
    email = (row.get("email") or "").strip()
    phone = (row.get("phone") or "").strip()
    gmaps = (row.get("google_maps_url") or "").strip()
    reviews_blob = row.get("reviews") or ""
    source = (row.get("source") or "google_places").strip()
    licenses = (row.get("license_codes") or "").strip()

    parts: list[str] = [f'<div style="font-family:system-ui,sans-serif;">']
    parts.append(f'<div style="font-size:14px;font-weight:600;margin-bottom:4px;">{name}</div>')

    rating_line = []
    if rating:
        rating_line.append(f"★ {rating}")
    if count:
        rating_line.append(f"{count} reviews")
    if distance:
        rating_line.append(f"{distance} km")
    if rating_line:
        parts.append(
            f'<div style="color:#555;font-size:12px;margin-bottom:6px;">'
            f'{" · ".join(html.escape(p) for p in rating_line)}</div>'
        )

    if address:
        parts.append(f'<div style="font-size:12px;margin-bottom:6px;">{address}</div>')

    links: list[str] = []
    if website:
        href = html.escape(website, quote=True)
        links.append(f'<a href="{href}" target="_blank" rel="noopener">Website</a>')
    if email:
        href = html.escape(email, quote=True)
        links.append(f'<a href="mailto:{href}">{html.escape(email)}</a>')
    if phone:
        links.append(f"📞 {html.escape(phone)}")
    if gmaps:
        href = html.escape(gmaps, quote=True)
        links.append(f'<a href="{href}" target="_blank" rel="noopener">Google Maps</a>')
    if links:
        parts.append(
            '<div style="font-size:12px;margin-bottom:8px;">'
            + " · ".join(links)
            + "</div>"
        )

    if licenses:
        parts.append(
            f'<div style="font-size:11px;color:#666;margin-bottom:6px;">'
            f'License: {html.escape(licenses)}</div>'
        )

    parts.append(
        f'<div style="font-size:10px;color:#888;margin-bottom:4px;">'
        f'Source: {html.escape(source)}</div>'
    )

    if reviews_blob:
        # The CSV stores reviews separated by blank lines (`\n\n`).
        chunks = [c.strip() for c in reviews_blob.split("\n\n") if c.strip()]
        for chunk in chunks[:2]:
            parts.append(
                '<div style="border-top:1px solid #eee;padding-top:6px;'
                'margin-top:6px;font-size:11px;color:#333;">'
                f"{html.escape(truncate(chunk, 220))}</div>"
            )

    parts.append("</div>")
    return "".join(parts)


LEGEND_HTML = """
<div style="
    position: fixed; bottom: 24px; left: 24px; z-index: 9999;
    background: rgba(255,255,255,0.95); padding: 10px 12px;
    border-radius: 6px; box-shadow: 0 1px 4px rgba(0,0,0,0.2);
    font-family: system-ui, sans-serif; font-size: 12px; line-height: 1.5;">
  <div style="font-weight:600;margin-bottom:4px;">Rating (color)</div>
  <div><span style="display:inline-block;width:10px;height:10px;
       background:#72b026;border-radius:50%;margin-right:6px;"></span>4.7+</div>
  <div><span style="display:inline-block;width:10px;height:10px;
       background:#38aadd;border-radius:50%;margin-right:6px;"></span>4.4 – 4.6</div>
  <div><span style="display:inline-block;width:10px;height:10px;
       background:#f69730;border-radius:50%;margin-right:6px;"></span>below 4.4</div>
  <div style="font-weight:600;margin-top:8px;margin-bottom:4px;">Source (icon)</div>
  <div><i class="fa fa-leaf" style="width:14px;text-align:center;margin-right:6px;color:#555;"></i>Google Places only</div>
  <div><i class="fa fa-home" style="width:14px;text-align:center;margin-right:6px;color:#555;"></i>agriturismo.it only</div>
  <div><i class="fa fa-star" style="width:14px;text-align:center;margin-right:6px;color:#555;"></i>both sources</div>
</div>
"""


def build_map(rows: list[dict]) -> folium.Map:
    lats = [r["_lat"] for r in rows]
    lngs = [r["_lng"] for r in rows]
    center = [sum(lats) / len(lats), sum(lngs) / len(lngs)]

    # Build with no default tile layer; we add two base layers below so the
    # user can toggle between satellite and street view via LayerControl.
    # OSM's volunteer tile servers are avoided because they require a Referer
    # header, which is missing when the HTML is opened directly from disk.
    m = folium.Map(location=center, zoom_start=11, tiles=None)

    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr="Tiles © Esri — Source: Esri, Maxar, Earthstar Geographics, and the GIS User Community",
        name="Satellite",
        overlay=False,
        control=True,
        max_zoom=19,
    ).add_to(m)
    # Place labels overlay (roads + city names) on top of the satellite imagery
    # so the user can still orient themselves geographically. It's a separate
    # transparent overlay that the user can toggle off.
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}",
        attr="Esri",
        name="Place labels (over satellite)",
        overlay=True,
        control=True,
        max_zoom=19,
    ).add_to(m)
    folium.TileLayer(
        tiles="CartoDB positron",
        name="Street map",
        overlay=False,
        control=True,
    ).add_to(m)

    cluster = MarkerCluster(name="Agriturismi").add_to(m)

    for row in rows:
        color = color_for_rating(row.get("rating", ""))
        icon_name = icon_for_source(row.get("source", ""))
        popup = folium.Popup(build_popup_html(row), max_width=380)
        tooltip = row.get("name") or ""
        folium.Marker(
            location=[row["_lat"], row["_lng"]],
            popup=popup,
            tooltip=tooltip,
            icon=folium.Icon(color=color, icon=icon_name, prefix="fa"),
        ).add_to(cluster)

    if len(rows) > 1:
        m.fit_bounds([[min(lats), min(lngs)], [max(lats), max(lngs)]], padding=(30, 30))

    m.get_root().html.add_child(folium.Element(LEGEND_HTML))
    folium.LayerControl(collapsed=True).add_to(m)
    return m


def main() -> None:
    parser = argparse.ArgumentParser(description="Render agriturismi CSVs as an HTML map.")
    parser.add_argument("inputs", nargs="+", help="CSV file paths or glob patterns")
    parser.add_argument(
        "--output",
        help="Output HTML path (default: map_<timestamp>.html)",
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

    print(f"Reading {len(paths)} file(s):")
    for p in paths:
        print(f"  - {p}")

    rows, skipped = collect_rows(paths)

    if skipped:
        print(
            f"\nSkipped {skipped} row(s) without lat/lng "
            "(CSVs from before the schema change)."
        )

    if not rows:
        print("\nNo plottable rows. Re-run main.py to generate CSVs with coordinates.")
        sys.exit(1)

    output = args.output or f"map_{datetime.now().strftime('%Y%m%d-%H%M%S')}.html"
    m = build_map(rows)
    m.save(output)

    print(f"\nWrote {len(rows)} markers to {output}")
    print(f"Open with: open {output}")


if __name__ == "__main__":
    main()
