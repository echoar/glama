# sql-spear: SQL & SPEAR Quality Gate Procedure

1. **PLAN:** State target table(s), date filter (max 1 day on high-volume event tables), join keys, and expected row granularity.
2. **SAFE SQL:** Always wrap division denominators in `NULLIF(denom, 0)` and cast integers to float (`* 1.0`) before dividing.
3. **ASSESS:** Check output rows for `NULL` dimension keys, `0.00` revenue with non-zero users, or missing date segments. Return `PASS` or `FAIL` with exact anomalies.
