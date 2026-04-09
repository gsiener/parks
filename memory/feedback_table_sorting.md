---
name: table_sorting
description: Preferred sort order for field availability tables
type: feedback
---

When displaying field availability tables, sort by:
1. Park name (alphabetical)
2. Field name by trailing number (numeric, not lexicographic) — e.g. Soccer-07 before Field-09 before Soccer-08, not alphabetically by full name

**Why:** User explicitly requested this — "Field-09" should sort after "Soccer-07" because 9 > 7, not before it because "F" < "S".

**How to apply:** Extract trailing number from field name for sort key, fall back to full name for fields without a number.
