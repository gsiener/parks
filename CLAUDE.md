# Parks Field Finder

Tool to find available NYC Parks Department fields for high school ultimate frisbee practice in Brooklyn.

## How it works

1. Fetches Brooklyn athletic field inventory from the Parks Dept vector tile layer (`maps.nycgovparks.org/athletic_facility/{z}/{x}/{y}`, gzip-compressed Mapbox vector tiles at zoom 13)
2. Fetches park name mappings by scraping the `spreadsheet-select` dropdown on the permit map page
3. For each practice time slot, queries the global availability API (`/api/athletic-fields?datetime=...`) at 30-minute intervals to find reserved fields
4. Cross-references to show which fields are open

All nycgovparks.org endpoints require a browser User-Agent header (they 403 otherwise). These are undocumented internal APIs used by the [permit map frontend](https://www.nycgovparks.org/permits/field-and-court/map).

## Key APIs

- **Global availability**: `GET https://www.nycgovparks.org/api/athletic-fields?datetime=YYYY-MM-DD+HH:MM` — returns `{"dusk": "20:00", "l": ["SYSTEM_ID", ...]}` (list of reserved fields)
- **Per-field schedule**: `GET https://www.nycgovparks.org/api/athletic-fields?location=SYSTEM_ID&date=YYYY-MM-DD` — returns 7-day schedule with 30-min slot detail
- **Per-park CSV**: `GET https://www.nycgovparks.org/permits/field-and-court/issued/{PARK_CODE}/csv` — all issued permits for a park
- **Vector tiles**: `GET https://maps.nycgovparks.org/athletic_facility/{z}/{x}/{y}` — field geometry and metadata (layers: `athletic_facility`, `athletic_facility_permitable`)

## Field system IDs

Format: `{PARK_CODE}-{ZONE?}-{SPORT}-{NUMBER}` (e.g., `B126-ZN04-SOCCER-2`). Borough prefix: B=Brooklyn, M=Manhattan, Q=Queens, X=Bronx, R=Staten Island.

Sport codes: SFB=Softball, BSB=Baseball, SCR=Soccer, FTB=Football, MPPA=Multi-purpose, CRK=Cricket, RBY=Rugby, BKB=Basketball, HDB=Handball, TNS=Tennis, VLB=Volleyball, BOC=Bocce, NTB=Netball, HKY=Hockey, TRK=Track.

## Practice schedule

- Tuesday & Thursday: 4:30 PM – 6:30 PM
- Saturday: 2:00 PM – 4:30 PM

## Excluded fields

Fields in `EXCLUDED_FIELDS` are known to be in unplayable condition despite appearing available in the permit system. Currently: Parade Ground Soccer-04, 04A, 04B.

## Dependencies

- Python 3.10+
- `mapbox-vector-tile` (`pip install mapbox-vector-tile`)
