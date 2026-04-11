---
title: "Overlapping permit flag causes false field availability"
problem_type: data_filtering_bug
component: find_fields.py
symptoms:
  - Fields shown as available ("Y") when physically blocked by overlapping permits
  - Baseball/PSAL reservations not reflected in soccer field availability
  - Inflated field availability counts
tags:
  - permits
  - overlapping-fields
  - availability
  - false-positive
  - slot-detail
related_apis:
  - "GET https://www.nycgovparks.org/api/athletic-fields?location=SYSTEM_ID&date=YYYY-MM-DD"
  - "GET https://www.nycgovparks.org/api/athletic-fields?datetime=YYYY-MM-DD+HH:MM"
---

## Root Cause

The Parks API includes permits with `permit_is_for_overlapping_field: True` when a physically co-located field (e.g., a baseball diamond sharing the same grass as a soccer field) is reserved. The `slot_detail()` function was filtering these out:

```python
if slot and not slot.get("permit_is_for_overlapping_field"):
```

This caused soccer fields to appear free even when an overlapping baseball permit blocked the physical space. The flag means "this permit was issued for a different but physically overlapping field" — not "ignore this permit."

Affected parks confirmed: Parade Ground, Red Hook Recreation Area, Commodore Barry Park (and likely others with multi-use grass space).

## Solution

In `slot_detail()` (~line 286), remove the overlapping-field guard:

```python
# Before (wrong):
if slot and not slot.get("permit_is_for_overlapping_field"):

# After (correct):
if slot:
```

This one-line change makes `slot_detail()` treat overlapping-field permits as real blocks. The result is more conservative field availability — which is correct. Fields that previously showed "Y" due to ignored baseball/PSAL permits now correctly show as blocked.

## Investigation

1. User reported soccer fields at Parade Ground (Soccer-1, 2, 5, 6, 7) share physical space with baseball diamonds. When baseball is reserved, soccer is unavailable — but the tool was showing "Y".
2. Fetched the per-field schedule for Parade Ground Soccer-02 via the API. All permits in the practice window had `permit_is_for_overlapping_field: True`.
3. Read `slot_detail()` — found the explicit filter at line 286 discarding those permits.
4. Removed the filter. Initially appeared too aggressive: Commodore Barry and McLaughlin also disappeared from available results.
5. Investigated: PSAL had Commodore Barry reserved via overlapping permits (4:30–6:30 PM window). User confirmed those parks also have physically overlapping baseball/softball diamonds — so blocking them is correct.
6. The more conservative results are accurate. The original "Y" results were inflated by the ignored permits.

## Prevention

**Default to inclusion, not exclusion.** A permit is a permit. If the API returns it, assume it blocks the field until proven otherwise. "I'm discarding permits where `permit_is_for_overlapping_field` is True" should prompt: "so if the field IS blocked, will the tool show it as available?"

**Treat unfamiliar API fields as signals, not noise.** `permit_is_for_overlapping_field: True` was added by the Parks API team because overlapping permits caused real problems. The field's existence is the signal.

**Document why a filter exists when you write it.** A comment like `# exclude X because Y` forces articulation of the reasoning. If you can't write a clear reason, the filter is probably wrong.

**Verify filters against known ground truth.** Before shipping a filter, find a real-world case where the filtered field is True and manually check what happened at that field on that date.

## Testing

**Known-occupied field check** — the most reliable test:

1. Find a field likely reserved on an upcoming weekend (Parade Ground on a Saturday afternoon).
2. Query the API directly: `GET /api/athletic-fields?location=SYSTEM_ID&date=YYYY-MM-DD`
3. Look for entries where `permit_is_for_overlapping_field: True`.
4. Run the tool for that date/time. If the field appears as available, the filter is wrong.

**Regression verification:**
- Re-add the old filter temporarily, run the tool, note which fields change availability.
- The fields that flip from blocked → available are the false positives the fix corrects.

**Ongoing gut-check:** If you arrive at a field the tool said was open and it's occupied, pull the raw API response for that field/time and look for what was missed.

## Additional Changes (Same Session)

- **Manhattan field support:** `fetch_brooklyn_fields()` tile bounds expanded to include Manhattan; field filter updated to `sid[0] in ("B", "M")`; `fetch_park_names()` updated to accept `M`-prefix codes.
- **Table sort:** Changed from `(commute_min, park_name, field_name)` to `(park_name, trailing_number_int, field_name)` so all fields for a park group together and sort numerically (Soccer-07 before Soccer-08, not lexicographically).
- **Park name overrides:** `B166C` → Coney Island Boat Basin, `B166D` → McGuire Fields, `B371` → Spring Creek Park (resolved by fetching `/parks/{code}` HTML).
