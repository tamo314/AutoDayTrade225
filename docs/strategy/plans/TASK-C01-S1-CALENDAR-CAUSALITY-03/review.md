# C01 S1 calendar-causality review

Task: \`TASK-C01-S1-CALENDAR-CAUSALITY-03\`  
Decision: **DONE for the frozen calendar/design scope only.**

| Completion criterion | Evidence |
|---|---|
| Fixed scope | Only the read-only C01 preparation contract and its declared official-source metadata are used.  No protected configuration/specification, registry, grant, manifest, runner, market file, OOS, or Final Holdout was read or changed. |
| Deterministic labels | \`label_audit.json\` hashes the immutable contract and has one row for every cash-open date, with the exact maximal prior closure, label, reason, trade-date check, and label totals.  The repair audit actually checked all 1,099 cash-open self mappings, all 32 confirmed-holiday next-cash-open mappings, and all 511 closed/non-holiday null mappings with zero errors. |
| Boundary cases | \`synthetic_cases.json\` fixes the 2022-09-23 plus weekend priority case, a weekend-only case, both mandated non-conducted holiday dates, the required pre-regime year-end/New-Year exclusion, a post-regime 2024-01-04 treatment-priority case, and missing/contradictory fail-closed cases.  Thus a year-end/New-Year name is not an override: only an interval with no confirmed holiday session is excluded after the regime begins. |
| As-of availability | Each audit row separates a pre-decision, dated scheduled notice from the final-list historical reconciliation snapshot.  The latter is never stated as historical execution-time knowledge. |
| Verification | The immutable S1 validator was rerun after this scoped repair and passed with exit code 0.  The price-free in-memory audit of all six synthetic case IDs also passed with exit code 0.  These structural PASS results are recorded in \`fixture_results.md\` and do not certify source truth, causality, or economics. |

## Access and cumulative limits

This repair performed no public-source retrieval: it reused only the frozen
contract and the public HTTPS URLs/publication metadata already preserved in
the read-only preparation artifacts.  The cumulative public-source ledger is
therefore unchanged at 21 query strings and 32 source-body review events; it
is a record, not a new search budget.  This task performed no market-data access,
real-data attempt, PnL computation, experiment dispatch, or grant/budget
operation.  Cumulative records therefore remain one C01 hypothesis and zero
real-data/PnL attempts; no closed-family search capacity was used or changed.

## Conclusion and remaining issues

C01 remains **NOT_EVALUATED** for economics, causality, feasibility, and
execution.  The only conclusion is that the sealed calendar yields reproducible
labels and that the audit does not backdate a later final list into prior
execution knowledge.  A later market stage would require its own authority and
is outside this completed task.

The final read-only audit confirmed 1,099 unique labelled cash-open dates,
the sealed contract SHA-256
\`6822242ec5f2f7b17bdf7248a13b7099fccb6fa15ded5bd834b90b241d642800\`,
HTTPS availability sources, dated pre-decision notices for all 677
post-introduction rows, and explicit \`OUT_OF_REGIME\` treatment for the 422
earlier rows.  It additionally confirms that 2023-01-04, 2024-01-04, and
2025-01-06 are post-regime treatment rows because their preceding intervals
contain confirmed 03 January holiday-trading rows; 2022-01-04 remains the
required pre-regime year-end/New-Year exclusion.  This resolves the condition
without changing the frozen label order, label totals, calendar contract, or
C01 hypothesis.  No issue remains within this frozen calendar-only scope.
