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

import re
import urllib.request
import urllib.parse
import json
import gzip
import math
import os
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

BOROUGH_PREFIXES = ("B", "M")
_TRAILING_NUM_RE = re.compile(r"(\d+)\D*$")

def _load_env():
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())

_load_env()
GOOGLE_MAPS_KEY = os.environ.get("GOOGLE_MAPS_KEY")

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
    "M144-ZN05-SOCCER-1",    # East River Park Soccer-04 East 6th St — under construction
    "M144-ZN05-SOCCER-2",    # East River Park Soccer-01A East 6th St — under construction
    "M144-ZN05-SOCCER-3",    # East River Park Soccer-01B East 6th St — under construction
}

# Parks to exclude by name substring (case-insensitive)
EXCLUDED_PARK_NAME_SUBSTRINGS = {"playground", "hamilton metz", "st. john's park", "lincoln terrace"}

# Park name overrides (park code -> display name)
PARK_NAME_OVERRIDES = {
    "B073": "Parade Ground",  # Parks system calls this "Prospect Park" but it's the same complex
    "B166C": "Coney Island Boat Basin",
    "B166D": "McGuire Fields",
    "B371": "Spring Creek Park",
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


def _haversine_transit_estimate(origin, dest):
    lat1, lon1 = math.radians(origin[0]), math.radians(origin[1])
    lat2, lon2 = math.radians(dest[0]), math.radians(dest[1])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    miles = EARTH_RADIUS_MI * 2 * math.asin(math.sqrt(a))
    return round(miles * TRANSIT_MIN_PER_MILE + TRANSIT_OVERHEAD_MIN)


_COMMUTE_CACHE_PATH = os.path.join(os.path.dirname(__file__), ".commute_cache.json")
_PARKS_CACHE_PATH = os.path.join(os.path.dirname(__file__), ".parks_cache.json")

def _load_json_cache(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def _save_json_cache(path, cache):
    with open(path, "w") as f:
        json.dump(cache, f, separators=(",", ":"))



def transit_minutes_estimate(origin, dest, cache=None):
    """Transit time in minutes from origin to dest (lat, lng).
    Uses Google Maps Distance Matrix API if key is available, else Haversine estimate.
    Pass cache dict to read/write cached results."""
    key = f"{dest[0]:.5f},{dest[1]:.5f}"
    if cache is not None and key in cache:
        return cache[key]
    if GOOGLE_MAPS_KEY:
        try:
            params = urllib.parse.urlencode({
                "origins": f"{origin[0]},{origin[1]}",
                "destinations": f"{dest[0]},{dest[1]}",
                "mode": "transit",
                "key": GOOGLE_MAPS_KEY,
            })
            url = f"https://maps.googleapis.com/maps/api/distancematrix/json?{params}"
            data = json.loads(urllib.request.urlopen(url, timeout=10).read())
            element = data["rows"][0]["elements"][0]
            if element["status"] == "OK":
                minutes = round(element["duration"]["value"] / 60)
                if cache is not None:
                    cache[key] = minutes
                return minutes
        except Exception:
            pass
    minutes = _haversine_transit_estimate(origin, dest)
    if cache is not None:
        cache[key] = minutes
    return minutes


def latlng_to_tile(lat, lng, zoom):
    n = 2**zoom
    x = int((lng + 180.0) / 360.0 * n)
    lat_rad = math.radians(lat)
    y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return x, y


def fetch_brooklyn_fields(cache=None):
    """Fetch all Brooklyn and Manhattan permitable fields from vector tiles."""
    if cache is not None and "fields" in cache:
        return cache["fields"]

    import mapbox_vector_tile

    zoom = 13
    # Brooklyn + Manhattan bounds
    corners = [
        (40.57, -74.04), (40.57, -73.86),  # Brooklyn
        (40.70, -74.02), (40.88, -73.91),  # Manhattan
    ]
    min_x = min_y = float("inf")
    max_x = max_y = 0
    for lat, lng in corners:
        x, y = latlng_to_tile(lat, lng, zoom)
        min_x, min_y = min(min_x, x), min(min_y, y)
        max_x, max_y = max(max_x, x), max(max_y, y)

    tiles = [(tx, ty) for tx in range(min_x, max_x + 1) for ty in range(min_y, max_y + 1)]

    def fetch_tile(tx, ty):
        url = f"https://maps.nycgovparks.org/athletic_facility/{zoom}/{tx}/{ty}"
        try:
            data = fetch(url)
            if data[:2] == b"\x1f\x8b":
                data = gzip.decompress(data)
            decoded = mapbox_vector_tile.decode(data)
            layer = decoded.get("athletic_facility_permitable", {})
            result = {}
            for feat in layer.get("features", []):
                props = feat.get("properties", {})
                sid = props.get("system", "")
                sport = props.get("primary_sport", "")
                if sid.startswith(BOROUGH_PREFIXES) and sport in SUITABLE_SPORTS and sid not in EXCLUDED_FIELDS:
                    centroid = geom_centroid(feat.get("geometry", {}), tx, ty, zoom)
                    if centroid:
                        props["_latlng"] = centroid
                    result[sid] = props
            return result
        except Exception:
            return {}

    fields = {}
    with ThreadPoolExecutor(max_workers=16) as executor:
        for result in executor.map(lambda t: fetch_tile(*t), tiles):
            for sid, props in result.items():
                fields.setdefault(sid, props)

    if cache is not None and fields:
        cache["fields"] = fields
    return fields


def fetch_park_names(cache=None):
    """Fetch park code -> park name mapping from the permit page."""
    if cache is not None and "park_names" in cache:
        return cache["park_names"]

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
            if code.startswith(BOROUGH_PREFIXES):
                names[code] = name

    if cache is not None and names:
        cache["park_names"] = names
    return names


def check_availability_at(dt_str, cache=None):
    """Query global availability API. Returns set of reserved field system IDs."""
    cache_key = f"avail:{dt_str}"
    if cache is not None and cache_key in cache:
        entry = cache[cache_key]
        return set(entry.get("l", [])), entry.get("dusk", "20:00")

    url = f"https://www.nycgovparks.org/api/athletic-fields?datetime={dt_str}"
    raw = fetch(url)
    if not raw:
        return set(), "20:00"
    data = json.loads(raw)
    if cache is not None:
        cache[cache_key] = data
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


def check_slot_availability(start_dt, end_dt, cache=None):
    """Check availability at 30-min intervals across a practice slot.
    A field is available only if it's free for the ENTIRE slot."""
    reserved_any = set()
    t = start_dt
    while t < end_dt:
        dt_str = t.strftime("%Y-%m-%d+%H:%M")
        try:
            reserved, dusk = check_availability_at(dt_str, cache=cache)
            reserved_any.update(reserved)
        except Exception as e:
            print(f"  Warning: failed to check {dt_str}: {e}")
        t += timedelta(minutes=30)
    return reserved_any


def fetch_field_schedule(sid, date_str, cache=None):
    """Fetch per-field 7-day schedule. Returns availability dict or {} on failure."""
    cache_key = f"sched:{sid}:{date_str}"
    if cache is not None and cache_key in cache:
        return cache[cache_key]

    url = f"https://www.nycgovparks.org/api/athletic-fields?location={sid}&date={date_str}"
    try:
        raw = fetch(url)
        if not raw:
            return {}
        data = json.loads(raw)
        result = data.get("availability", {})
        if cache is not None and result:
            cache[cache_key] = result
        return result
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
        if slot:  # includes permit_is_for_overlapping_field — overlapping fields block shared physical space
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

    def field_sort_key(sid, f):
        park_code = f.get("permit_parent", f.get("gispropnum", ""))
        fname = f.get("name", "")
        m = _TRAILING_NUM_RE.search(fname)
        trailing = int(m.group(1)) if m else 0
        return (park_name_cache[park_code], trailing, fname)

    park_name_cache = {
        f.get("permit_parent", f.get("gispropnum", "")): park_display_name(
            f.get("permit_parent", f.get("gispropnum", "")), park_names
        )
        for f in fields.values()
    }

    rows = []
    for sid, f in sorted(fields.items(), key=lambda x: field_sort_key(*x)):
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
    parser.add_argument("--no-cache", action="store_true", help="Ignore cached API responses and fetch fresh data")
    args = parser.parse_args()

    start = datetime.strptime(args.date, "%Y-%m-%d") if args.date else datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    parks_cache = {} if args.no_cache else _load_json_cache(_PARKS_CACHE_PATH)

    print("Loading field inventory...")
    fields = fetch_brooklyn_fields(cache=parks_cache)
    park_names = fetch_park_names(cache=parks_cache)

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

    # Compute driving times, using disk cache to avoid redundant API calls
    print(f"Computing commute times from Brooklyn Tech (max {args.max_commute} min drive)...")
    commute_cache = _load_json_cache(_COMMUTE_CACHE_PATH)
    for sid, f in fields.items():
        if "_latlng" in f:
            f["_commute_min"] = transit_minutes_estimate(ORIGIN, f["_latlng"], cache=commute_cache)
    _save_json_cache(_COMMUTE_CACHE_PATH, commute_cache)

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
        # Collect one anchor date per 7-day window needed to cover all practice slots
        first = practice_dates[0][0]
        anchor_dates = sorted({
            (first + timedelta(days=7 * ((slot - first).days // 7))).strftime("%Y-%m-%d")
            for slot, _ in practice_dates
        })

        all_jobs = [(sid, anchor) for sid in fields for anchor in anchor_dates]
        uncached = [(sid, anchor) for sid, anchor in all_jobs
                    if f"sched:{sid}:{anchor}" not in parks_cache]
        if uncached:
            print(f"Fetching field schedules ({len(uncached)} requests, {len(all_jobs) - len(uncached)} cached)...")
        else:
            print(f"Loading field schedules ({len(all_jobs)} cached)...")

        schedules = {}
        for sid, anchor in all_jobs:
            cached = parks_cache.get(f"sched:{sid}:{anchor}")
            if cached:
                schedules.setdefault(sid, {}).update(cached)
        if uncached:
            with ThreadPoolExecutor(max_workers=len(uncached)) as executor:
                futs = {executor.submit(fetch_field_schedule, sid, anchor, parks_cache): (sid, anchor)
                        for sid, anchor in uncached}
                for fut in as_completed(futs):
                    sid, _ = futs[fut]
                    schedules.setdefault(sid, {}).update(fut.result())

        # Build per-field per-slot status from per-field schedule data
        field_statuses = {}
        for sid in fields:
            schedule = schedules.get(sid, {})
            field_statuses[sid] = [
                slot_detail(schedule, slot_start, slot_end)
                for slot_start, slot_end in practice_dates
            ]

        print()
        print_table(practice_dates, field_statuses, fields, park_names)
        _save_json_cache(_PARKS_CACHE_PATH, parks_cache)
        return

    for slot_start, slot_end in practice_dates:
        day = DAY_NAMES[slot_start.isoweekday()]
        date_str = slot_start.strftime("%a %b %d")
        time_str = f"{slot_start.strftime('%-I:%M')}-{slot_end.strftime('%-I:%M %p')}"
        print(f"{'=' * 60}")
        print(f"  {date_str}  {time_str}")
        print(f"{'=' * 60}")

        reserved = check_slot_availability(slot_start, slot_end, cache=parks_cache)

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

    _save_json_cache(_PARKS_CACHE_PATH, parks_cache)


if __name__ == "__main__":
    main()
