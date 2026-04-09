---
name: excluded_fields
description: Fields excluded from results due to unplayable or under-construction conditions
type: feedback
---

Exclude these fields from availability results — they are unplayable or unavailable:

**Parade Ground (unplayable condition):**
- B068-SOCCER-1 (Soccer-04)
- B073-ZN28-SOCCER-4A (Soccer-04A)
- B073-ZN28-SOCCER-4B (Soccer-04B)

**Red Hook Recreation Area (under construction as of 2026-04-09):**
- B126-ZN06-SOCCER-1 (Soccer-01)
- B126-ZN07-SOCCER-1 (Soccer-06)

**Why:** The permit system shows these fields as available but they are physically unusable. Red Hook Soccer-01 and Soccer-06 are under construction. Parade Ground Soccer-04/04A/04B are in unplayable condition.

**How to apply:** These are in EXCLUDED_FIELDS in find_fields.py. If the user flags more fields, add them here and to the code.
