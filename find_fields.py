#!/usr/bin/env python3
"""
Find available NYC Parks fields for ultimate frisbee practice.
Checks Brooklyn soccer/football/multipurpose fields against the
Parks Department permit system for upcoming practice slots.

Usage:
    python3 find_fields.py                  # next 2 weeks of practices
    python3 find_fields.py --weeks 4        # next 4 weeks
    python3 find_fields.py --date 2026-04-07  # specific week starting date
"""

import urllib.request
import json
import gzip
import math
import sys
from datetime import datetime, timedelta
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Practice schedule: (weekday number per Python's isoweekday(), start_hour, start_min, end_hour, end_min)
# Monday=1, Tuesday=2, ... Sunday=7
PRACTICE_SLOTS = [
    (2, 16, 30, 18, 30),  # Tuesday 4:30-6:30 PM
    (4, 16, 30, 18, 30),  # Thursday 4:30-6:30 PM
    (6, 14, 0, 16, 30),   # Saturday 2:00-4:30 PM
]

DAY_NAMES = {1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu", 5: "Fri", 6: "Sat", 7: "Sun"}

# Fields to exclude (known unplayable conditions, etc.)
EXCLUDED_FIELDS = {
    "B068-SOCCER-1",   # Parade Ground Soccer-04 — unplayable
    "B073-ZN28-SOCCER-4A",  # Parade Ground Soccer-04A — unplayable
    "B073-ZN28-SOCCER-4B",  # Parade Ground Soccer-04B — unplayable
}

# Sports suitable for ultimate frisbee
SUITABLE_SPORTS = {"FTB", "SCR", "MPPA", "RBY", "CRK"}

# We prefer real fields over asphalt MPPA courts
PREFERRED_SURFACES = {"Synthetic - Large/Full", "Synthetic - Multi", "Natural"}


def fetch(url):
    """Fetch URL with browser User-Agent."""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    resp = urllib.request.urlopen(req, timeout=15)
    return resp.read()


def latlng_to_tile(lat, lng, zoom):
    n = 2**zoom
    x = int((lng + 180.0) / 360.0 * n)
    lat_rad = math.radians(lat)
    y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return x, y


def fetch_brooklyn_fields():
    """Fetch all Brooklyn permitable fields from vector tiles."""
    import mapbox_vector_tile

    zoom = 13
    # Brooklyn bounds
    corners = [(40.57, -74.04), (40.57, -73.86), (40.74, -74.04), (40.74, -73.86)]
    min_x = min_y = float("inf")
    max_x = max_y = 0
    for lat, lng in corners:
        x, y = latlng_to_tile(lat, lng, zoom)
        min_x, min_y = min(min_x, x), min(min_y, y)
        max_x, max_y = max(max_x, x), max(max_y, y)

    fields = {}
    for tx in range(min_x, max_x + 1):
        for ty in range(min_y, max_y + 1):
            url = f"https://maps.nycgovparks.org/athletic_facility/{zoom}/{tx}/{ty}"
            try:
                data = fetch(url)
                if data[:2] == b"\x1f\x8b":
                    data = gzip.decompress(data)
                decoded = mapbox_vector_tile.decode(data)
                layer = decoded.get("athletic_facility_permitable", {})
                for feat in layer.get("features", []):
                    props = feat.get("properties", {})
                    sid = props.get("system", "")
                    sport = props.get("primary_sport", "")
                    if sid.startswith("B") and sport in SUITABLE_SPORTS and sid not in fields and sid not in EXCLUDED_FIELDS:
                        fields[sid] = props
            except Exception:
                pass
    return fields


def fetch_park_names():
    """Fetch park code -> park name mapping from the permit page."""
    import re

    html = fetch("https://www.nycgovparks.org/permits/field-and-court/map").decode("utf-8")
    select_match = re.search(
        r"<select[^>]*id=[\"']spreadsheet-select[\"'][^>]*>(.*?)</select>",
        html,
        re.DOTALL,
    )
    names = {}
    if select_match:
        for code, name in re.findall(
            r"<option\s+value=[\"']([^\"'>]*)[\"'][^>]*>([^<]*)</option>",
            select_match.group(1),
        ):
            if code.startswith("B"):
                names[code] = name
    return names


def check_availability_at(dt_str):
    """Query global availability API. Returns set of reserved field system IDs."""
    url = f"https://www.nycgovparks.org/api/athletic-fields?datetime={dt_str}"
    data = json.loads(fetch(url))
    return set(data.get("l", [])), data.get("dusk", "20:00")


def get_practice_dates(start_date, weeks):
    """Generate practice date/times for the given number of weeks."""
    dates = []
    for week in range(weeks):
        for dow, sh, sm, eh, em in PRACTICE_SLOTS:
            # Find the next occurrence of this weekday
            d = start_date + timedelta(days=week * 7)
            while d.isoweekday() != dow:
                d += timedelta(days=1)
            start_dt = d.replace(hour=sh, minute=sm, second=0, microsecond=0)
            end_dt = d.replace(hour=eh, minute=em, second=0, microsecond=0)
            if start_dt >= datetime.now():
                dates.append((start_dt, end_dt))
    return sorted(dates)


def check_slot_availability(start_dt, end_dt):
    """Check availability at 30-min intervals across a practice slot.
    A field is available only if it's free for the ENTIRE slot."""
    reserved_any = set()
    t = start_dt
    while t < end_dt:
        dt_str = t.strftime("%Y-%m-%d+%H:%M")
        try:
            reserved, dusk = check_availability_at(dt_str)
            reserved_any.update(reserved)
        except Exception as e:
            print(f"  Warning: failed to check {dt_str}: {e}")
        t += timedelta(minutes=30)
    return reserved_any


def surface_label(surface):
    labels = {
        "Synthetic - Large/Full": "synth",
        "Synthetic - Multi": "synth-multi",
        "Natural": "grass",
        "Asphalt": "asphalt",
    }
    return labels.get(surface, surface or "?")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Find available fields for ultimate frisbee practice")
    parser.add_argument("--weeks", type=int, default=2, help="Number of weeks to check (default: 2)")
    parser.add_argument("--date", type=str, help="Start date YYYY-MM-DD (default: today)")
    parser.add_argument("--all-surfaces", action="store_true", help="Include asphalt MPPA fields")
    args = parser.parse_args()

    start = datetime.strptime(args.date, "%Y-%m-%d") if args.date else datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    print("Loading Brooklyn field inventory...")
    fields = fetch_brooklyn_fields()
    park_names = fetch_park_names()

    # Filter out asphalt MPPA unless --all-surfaces
    if not args.all_surfaces:
        fields = {
            sid: f for sid, f in fields.items()
            if f.get("surface_type", "") in PREFERRED_SURFACES
        }

    print(f"Tracking {len(fields)} fields (soccer/football/multipurpose/cricket/rugby)")
    print()

    practice_dates = get_practice_dates(start, args.weeks)
    if not practice_dates:
        print("No upcoming practice slots found.")
        return

    for slot_start, slot_end in practice_dates:
        day = DAY_NAMES[slot_start.isoweekday()]
        date_str = slot_start.strftime("%a %b %d")
        time_str = f"{slot_start.strftime('%-I:%M')}-{slot_end.strftime('%-I:%M %p')}"
        print(f"{'=' * 60}")
        print(f"  {date_str}  {time_str}")
        print(f"{'=' * 60}")

        reserved = check_slot_availability(slot_start, slot_end)

        # Find available fields
        available = {}
        unavailable_count = 0
        for sid, f in fields.items():
            if sid in reserved:
                unavailable_count += 1
            else:
                park_code = f.get("permit_parent", f.get("gispropnum", "???"))
                available.setdefault(park_code, []).append(f)

        # Sort parks by number of available fields (most first)
        sorted_parks = sorted(available.items(), key=lambda x: -len(x[1]))

        print(f"  {len(fields) - unavailable_count} of {len(fields)} fields available")
        print()

        for park_code, park_fields in sorted_parks:
            park_name = park_names.get(park_code, park_code)
            field_strs = []
            for f in sorted(park_fields, key=lambda x: x.get("system", "")):
                name = f.get("name", "?")
                surface = surface_label(f.get("surface_type", ""))
                sport = f.get("primary_sport", "")
                field_strs.append(f"{name} [{surface}]")

            print(f"  {park_name} ({park_code}) — {len(park_fields)} field(s)")
            for fs in field_strs:
                print(f"    {fs}")
            print()

        print()


if __name__ == "__main__":
    main()
