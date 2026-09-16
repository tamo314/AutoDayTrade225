# C01 S1 fixed-fixture results

Status: **calendar-only structural verification; C01 economics remains
\`NOT_EVALUATED\`.**

The fixture set fixes treatment priority, weekend-only exclusion, the two
known non-conducted holiday dates (2023-11-03 and 2024-08-12), a pre-regime
year-end/New-Year exclusion, a post-regime year-end/New-Year treatment-priority
case, and fail-closed missing/contradictory input.  No fixture contains a
price, market-derived count, PnL, order, fill, or trade.

The audit algorithm uses only the sealed contract's \`cash_open\` and
\`ose_holiday_trading\` fields to reconstruct the maximal preceding
cash-closed interval.  It checks that every cash-open date receives exactly
one label, the closure interval is exact, treatment outranks a weekend, and
cash-open/confirmed-holiday trade-date mappings are internally consistent.

Expected audit totals from the sealed input are:

| Label | Calendar-date count |
|---|---:|
| \`TREATMENT_HOLIDAY_REOPEN\` | 28 |
| \`ORDINARY_CASH_OPEN_CONTROL\` | 523 |
| \`OUT_OF_REGIME_OR_EXCLUDED\` | 548 |
| Total cash-open dates | 1,099 |

The published final-list XLSX is a 2026-09-16 historical reconciliation
snapshot, not retrospective execution information.  Each post-introduction
row also records the pre-decision dated notice selected for its calendar
release period; pre-regime rows are explicitly out of regime.  This is a
provenance distinction, not proof of what any historical operation consumed.

Validation command (run after the generated audit is complete):

\`\`\`powershell
.venv/Scripts/python.exe scripts/validate_c01_s1_calendar_causality.py --artifacts docs/strategy/plans/TASK-C01-S1-CALENDAR-CAUSALITY-03
\`\`\`

Actual fixed-validator result: **PASS, exit code 0**.

## Execution and repair log

1. The first mechanical expansion of the sealed calendar produced all 1,099
   cash-open rows but left the exclusion branch's label value stale.  The fixed
   validator failed (exit code 1) at 2022-10-03: its interval and exclusion
   reason were present, but its label was incorrectly
   \`ORDINARY_CASH_OPEN_CONTROL\`.
2. The audit JSON alone was repaired by reapplying the immutable validator's
   \`expected_labels\` rule to the sealed calendar and regenerating its label,
   interval, reason, and counts.  No calendar row, source URL, rule,
   synthetic case, market input, or protected file changed.
3. The second fixed-validator run passed (exit code 0): 1,099 unique
   cash-open rows, 28 treatment rows, 523 ordinary controls, and 548 excluded
   rows.
4. A final read-only cross-artifact audit passed: the input SHA-256 is
   \`6822242ec5f2f7b17bdf7248a13b7099fccb6fa15ded5bd834b90b241d642800\`;
   every audit row has a matching deterministic label/interval, HTTPS
   provenance, and the required as-of/final-list separation.  It also checked
   the five required synthetic-case IDs and that this directory contains only
   the five declared artifacts.  The fixed validator was then rerun and again
   passed with exit code 0.
5. This repair resolved the apparent year-end/New-Year conflict without a new
   label rule: the required excluded fixture remains 2022-01-04 (pre-regime),
   while the new fixed post-regime fixture is 2024-01-04 (the 2024-01-03
   confirmed holiday session makes it treatment).  The same full-contract
   check records 2023-01-04 and 2025-01-06 as treatment for the corresponding
   03 January confirmed sessions.  The repair audit checked all 1,099
   cash-open self trade-date mappings, all 32 confirmed-holiday mappings to
   the next cash-open date, and all 511 closed/non-holiday null mappings;
   each error count was zero.  It also rechecked 1,099 unique labels and the
   unchanged 28 / 523 / 548 treatment / control / excluded totals.  No market
   input was opened.
6. The required immutable validator was rerun after this repair and passed
   with exit code 0.  A separate in-memory, price-free fixture audit also
   passed with exit code 0: all six case IDs (including the added
   \`post_regime_new_year_treatment_priority\` case) matched their expected
   labels and expected \`ose_trade_date\` values; the missing/contradictory
   fixture remained fail-closed with no asserted trade date.

Limitations: the fixed validator checks structure only.  It does not validate
official-source truth, an actual historical notification workflow, causality,
observability, execution, sample adequacy, or economic performance.
