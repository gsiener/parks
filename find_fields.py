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
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Commute origin: Brooklyn Tech High School, Fort Greene
ORIGIN = (40.6916, -73.9762)

EARTH_RADIUS_MI = 3958.8
TRANSIT_OVERHEAD_MIN = 10   # fixed walk-to/from-station overhead
TRANSIT_MIN_PER_MILE = 6    # ~10 mph door-to-door in Brooklyn

# Practice schedule: (weekday number per Python's isoweekday(), start_hour, start_min, end_hour, end_min)
# Monday=1, Tuesday=2, ... Sunday=7
PRACTICE_SLOTS = [
    (1, 16, 30, 18, 30),  # Monday 4:30-6:30 PM
    (2, 16, 30, 18, 30),  # Tuesday 4:30-6:30 PM
    (3, 16, 30, 18, 30),  # Wednesday 4:30-6:30 PM
    (4, 16, 30, 18, 30),  # Thursday 4:30-6:30 PM
    (6, 14, 0, 16, 30),   # Saturday 2:00-4:30 PM
]

DAY_NAMES = {1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu", 5: "Fri", 6: "Sat", 7: "Sun"}

# Fields to exclude (known unplayable conditions, etc.)
EXCLUDED_FIELDS = {
    "B068-SOCCER-1",         # Parade Ground Soccer-04 — unplayable
    "B073-ZN28-SOCCER-4A",   # Parade Ground Soccer-04A — unplayable
    "B073-ZN28-SOCCER-4B",   # Parade Ground Soccer-04B — unplayable
    "B126-ZN06-SOCCER-1",    # Red Hook Soccer-01 — under construction
    "B126-ZN07-SOCCER-1",    # Red Hook Soccer-06 — under construction
}

# Parks to exclude by name substring (case-insensitive)
EXCLUDED_PARK_NAME_SUBSTRINGS = {"playground", "hamilton metz", "st. john's park", "lincoln terrace"}

# Park name overrides (park code -> display name)
PARK_NAME_OVERRIDES = {
    "B073": "Parade Ground",  # Parks system calls this "Prospect Park" but it's the same complex
}

# Parks to exclude by park code
EXCLUDED_PARKS = {
    "B270",  # Brownsville Playground
    "B372",  # Friends Field — too far
    "B377",  # Floyd Patterson Ballfields — too far
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


def tile_pixel_to_latlng(px, py, tx, ty, zoom, extent=4096):
    """Convert tile-local pixel coordinates to lat/lng."""
    n = 2 ** zoom
    lon = (tx + px / extent) / n * 360.0 - 180.0
    merc_n = math.pi - 2 * math.pi * (ty + py / extent) / n
    lat = math.degrees(math.atan(math.sinh(merc_n)))
    return lat, lon


def geom_centroid(geometry, tx, ty, zoom):
    """Extract approximate lat/lng centroid from tile geometry."""
    geom_type = geometry.get("type", "")
    coords = geometry.get("coordinates", [])
    ring = None
    if geom_type == "Polygon" and coords:
        ring = coords[0]
    elif geom_type == "MultiPolygon" and coords and coords[0]:
        ring = coords[0][0]
    elif geom_type == "Point":
        return tile_pixel_to_latlng(coords[0], coords[1], tx, ty, zoom)
    if ring:
        cx = sum(c[0] for c in ring) / len(ring)
        cy = sum(c[1] for c in ring) / len(ring)
        return tile_pixel_to_latlng(cx, cy, tx, ty, zoom)
    return None


def transit_minutes_estimate(origin, dest):
    """Estimate transit time in minutes using Haversine distance + NYC transit factor.
    Assumes ~10 mph door-to-door average speed in Brooklyn + 10 min fixed overhead."""
    lat1, lon1 = math.radians(origin[0]), math.radians(origin[1])
    lat2, lon2 = math.radians(dest[0]), math.radians(dest[1])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    miles = EARTH_RADIUS_MI * 2 * math.asin(math.sqrt(a))
    return round(miles * TRANSIT_MIN_PER_MILE + TRANSIT_OVERHEAD_MIN)


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
                        centroid = geom_centroid(feat.get("geometry", {}), tx, ty, zoom)
                        if centroid:
                            props["_latlng"] = centroid
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


def fetch_field_schedule(sid, date_str):
    """Fetch per-field 7-day schedule. Returns availability dict or {} on failure."""
    url = f"https://www.nycgovparks.org/api/athletic-fields?location={sid}&date={date_str}"
    try:
        data = json.loads(fetch(url))
        return data.get("availability", {})
    except Exception:
        return {}


def slot_detail(schedule, start_dt, end_dt):
    """Analyze a slot from per-field schedule data.

    Returns (status, data):
      ('free', None)
      ('pending_unnamed', count)          — requests exist, no names visible
      ('pending_named', {holders})        — named pending permit (not yet issued)
      ('reserved', {holders})             — confirmed issued permit
    """
    t = start_dt
    confirmed = set()
    pending_named = set()
    max_unnamed = 0
    while t < end_dt:
        ts = str(int(t.timestamp()))
        slot = schedule.get(ts, {})
        if slot and not slot.get("permit_is_for_overlapping_field"):
            holder = slot.get("permit_holder") or ""
            if slot.get("is_issued") and holder:
                confirmed.add(holder)
            elif not slot.get("is_issued") and holder:
                pending_named.add(holder)
            max_unnamed = max(max_unnamed, slot.get("num_pending_permits", 0))
        t += timedelta(minutes=30)

    if confirmed:
        return ("reserved", confirmed)
    if pending_named:
        return ("pending_named", pending_named)
    if max_unnamed:
        return ("pending_unnamed", max_unnamed)
    return ("free", None)


def park_display_name(park_code, park_names):
    return PARK_NAME_OVERRIDES.get(park_code) or park_names.get(park_code, park_code)


def surface_label(surface):
    labels = {
        "Synthetic - Large/Full": "synth",
        "Synthetic - Multi": "synth-multi",
        "Natural": "grass",
        "Asphalt": "asphalt",
    }
    return labels.get(surface, surface or "?")


def print_table(slots, field_statuses, fields, park_names):
    """Print a cross-slot availability table.
    field_statuses: {sid: [('free'|'pending'|'issued', detail), ...]} per slot
    """
    headers = [s.strftime("%a %-m/%-d") for s, _ in slots]
    time_labels = [f"{s.strftime('%-I:%M')}-{e.strftime('%-I:%M %p')}" for s, e in slots]

    rows = []
    for sid, f in sorted(fields.items(), key=lambda x: (x[1].get("_commute_min", 999), x[1].get("permit_parent", ""), x[1].get("name", ""))):
        statuses = field_statuses.get(sid, [("reserved", None)] * len(slots))
        if any(s != "reserved" for s, _ in statuses):
            park_code = f.get("permit_parent", f.get("gispropnum", "???"))
            park_name = park_display_name(park_code, park_names)
            name = f.get("name", "?")
            surface = surface_label(f.get("surface_type", ""))
            commute = f.get("_commute_min")
            commute_str = f"{commute}m" if commute is not None else "?"
            rows.append((park_name, name, surface, commute_str, statuses))

    if not rows:
        print("  No fields available within commute limit.")
        return

    # Collect footnotes for named pending permits
    footnotes = {}  # ref_char -> org name
    ref_chars = "abcdefghijklmnopqrstuvwxyz"

    def org_ref(org):
        for ref, name in footnotes.items():
            if name == org:
                return ref
        ref = ref_chars[len(footnotes)]
        footnotes[ref] = org
        return ref

    def cell_raw(status, detail):
        if status == "free":
            return "Y"
        elif status == "pending_unnamed":
            return f"P({detail})"
        elif status == "pending_named":
            refs = ",".join(sorted(org_ref(o) for o in detail))
            return f"P[{refs}]"
        return "-"

    # Pre-assign footnote refs so they're stable during the single render pass
    for _, _, _, _, statuses in rows:
        for status, detail in statuses:
            if status == "pending_named":
                for org in detail:
                    org_ref(org)

    park_w = max(len("Park"), max(len(r[0]) for r in rows))
    field_w = max(len("Field"), max(len(r[1]) for r in rows))
    surf_w = max(len("Surface"), max(len(r[2]) for r in rows))
    comm_w = max(len("Transit"), max(len(r[3]) for r in rows))
    slot_w = max(
        max(len(h) for h in headers),
        max(len(cell_raw(s, d)) for _, _, _, _, statuses in rows for s, d in statuses),
    )

    rendered = []
    for park, field, surf, comm, statuses in rows:
        rendered.append([cell_raw(s, d) for s, d in statuses])

    def row_str(park, field, surf, comm, cells):
        return f"  {park:<{park_w}}  {field:<{field_w}}  {surf:<{surf_w}}  {comm:>{comm_w}}  " + "  ".join(c.center(slot_w) for c in cells)

    sep = ("  " + "-" * park_w + "  " + "-" * field_w + "  " + "-" * surf_w + "  " +
           "-" * comm_w + "  " + "  ".join("-" * slot_w for _ in slots))

    print("  " + " " * park_w + "  " + " " * field_w + "  " + " " * surf_w + "  " + " " * comm_w + "  " + "  ".join(t.center(slot_w) for t in time_labels))
    print(f"  {'Park':<{park_w}}  {'Field':<{field_w}}  {'Surface':<{surf_w}}  {'Transit':>{comm_w}}  " + "  ".join(h.center(slot_w) for h in headers))
    print(sep)

    cur_park = None
    for (park, field, surf, comm, statuses), cells in zip(rows, rendered):
        if park != cur_park:
            if cur_park is not None:
                print()
            cur_park = park
        print(row_str(park, field, surf, comm, cells))

    print()
    print("  Y=free  P(N)=N unnamed pending  P[x]=named pending  -[x]=reserved")
    print("  Transit = estimated door-to-door transit time from Brooklyn Tech")
    if footnotes:
        print()
        print("  Orgs:")
        for ref, name in sorted(footnotes.items()):
            print(f"    [{ref}] {name}")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Find available fields for ultimate frisbee practice")
    parser.add_argument("--weeks", type=int, default=2, help="Number of weeks to check (default: 2)")
    parser.add_argument("--date", type=str, help="Start date YYYY-MM-DD (default: today)")
    parser.add_argument("--all-surfaces", action="store_true", help="Include asphalt MPPA fields")
    parser.add_argument("--table", action="store_true", help="Output as a cross-slot availability table")
    parser.add_argument("--max-commute", type=int, default=35, help="Max driving minutes from Brooklyn Tech (default: 35)")
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

    # Filter out excluded parks (by code and by name substring)
    def park_excluded(f):
        code = f.get("permit_parent", f.get("gispropnum", ""))
        if code in EXCLUDED_PARKS:
            return True
        name = park_names.get(code, "")
        return any(sub in name.lower() for sub in EXCLUDED_PARK_NAME_SUBSTRINGS)

    fields = {sid: f for sid, f in fields.items() if not park_excluded(f)}

    # Compute driving times in parallel and filter by max commute
    print(f"Computing commute times from Brooklyn Tech (max {args.max_commute} min drive)...")
    for sid, f in fields.items():
        if "_latlng" in f:
            f["_commute_min"] = transit_minutes_estimate(ORIGIN, f["_latlng"])

    fields = {
        sid: f for sid, f in fields.items()
        if f.get("_commute_min", 999) <= args.max_commute
    }

    print(f"Tracking {len(fields)} fields within {args.max_commute} min drive of Brooklyn Tech")
    print()

    practice_dates = get_practice_dates(start, args.weeks)
    if not practice_dates:
        print("No upcoming practice slots found.")
        return

    if args.table:
        anchor_date = practice_dates[0][0].strftime("%Y-%m-%d")
        print("Checking availability and fetching field schedules...")
        with ThreadPoolExecutor(max_workers=10) as executor:
            slot_futures = [
                executor.submit(check_slot_availability, s, e)
                for s, e in practice_dates
            ]
            schedule_futures = {
                executor.submit(fetch_field_schedule, sid, anchor_date): sid
                for sid in fields
            }
            slot_reserved = [f.result() for f in slot_futures]
            schedules = {schedule_futures[f]: f.result() for f in as_completed(schedule_futures)}

        # Build per-field per-slot status from per-field schedule data
        field_statuses = {}
        for sid in fields:
            schedule = schedules.get(sid, {})
            statuses = []
            for i, (slot_start, slot_end) in enumerate(practice_dates):
                if schedule:
                    statuses.append(slot_detail(schedule, slot_start, slot_end))
                elif sid in slot_reserved[i]:
                    statuses.append(("reserved", set()))
                else:
                    statuses.append(("free", None))
            field_statuses[sid] = statuses

        print()
        print_table(practice_dates, field_statuses, fields, park_names)
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
            park_name = park_display_name(park_code, park_names)
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
