# Parks Field Finder

Find available NYC Parks Department athletic fields for ultimate frisbee practice, filtered by commute time from Brooklyn Tech.

## Usage

```
python find_fields.py                        # next 2 weeks, table view
python find_fields.py --table                # cross-slot availability table
python find_fields.py --weeks 4              # look 4 weeks out
python find_fields.py --date 2026-04-15      # start from a specific date
python find_fields.py --max-commute 60       # widen commute limit (default: 35 min)
python find_fields.py --all-surfaces         # include asphalt multi-purpose courts
```

Example output:

```
                                                4:30-6:30 PM  4:30-6:30 PM  2:00-4:30 PM
  Park           Field        Surface  Transit  Tue 4/14  Wed 4/15  Sat 4/18
  -------------  -----------  -------  -------  --------  --------  --------
  Parade Ground  Soccer-08    synth        17m     Y         -         -
  Wingate Park   Football-01  grass        28m     -         -         Y

  Y=free  P(N)=N unnamed pending  P[x]=named pending  -=reserved
```

## Setup

```
pip install mapbox-vector-tile
```

For accurate transit times, add a Google Maps API key to `.env`:

```
GOOGLE_MAPS_KEY=your_key_here
```

Without it, commute times fall back to a straight-line distance estimate.

## Practice schedule

- Monday, Tuesday, Thursday: 4:30–6:30 PM
- Saturday: 2:00–4:30 PM

## How it works

1. Fetches Brooklyn and Manhattan athletic field inventory from the Parks Dept vector tile layer (Mapbox tiles at zoom 13, gzip-compressed)
2. Filters to fields within the commute limit from Brooklyn Tech using the Google Maps Distance Matrix API
3. Fetches per-field 7-day permit schedules, covering enough windows to span the full date range requested
4. For each field and practice slot, checks for issued permits — including permits on physically overlapping fields (e.g. a baseball diamond sharing the same grass as a soccer field)

All `nycgovparks.org` endpoints require a browser User-Agent (they 403 otherwise). These are undocumented internal APIs used by the [Parks permit map](https://www.nycgovparks.org/permits/field-and-court/map).

## Key APIs

| API | Description |
|-----|-------------|
| `GET /api/athletic-fields?location=SYSTEM_ID&date=YYYY-MM-DD` | Per-field 7-day permit schedule |
| `GET /permits/field-and-court/issued/{PARK_CODE}/csv` | All issued permits for a park |
| `GET maps.nycgovparks.org/athletic_facility/{z}/{x}/{y}` | Field geometry and metadata (vector tiles) |

## Field system IDs

Format: `{PARK_CODE}-{ZONE?}-{SPORT}-{NUMBER}` (e.g. `B073-ZN15-SOCCER-2`)

Borough prefix: `B`=Brooklyn, `M`=Manhattan, `Q`=Queens, `X`=Bronx, `R`=Staten Island

Sport codes: `SCR`=Soccer, `FTB`=Football, `MPPA`=Multi-purpose, `SFB`=Softball, `BSB`=Baseball, `CRK`=Cricket, `RBY`=Rugby
