# C01 preparation review — TASK-C01-PREPARATION-02

## Completion decision

**Task decision: DONE (preparation scope only).**  This means every declared
documentation/public-source, calendar, correction, and permitted synthetic
validation deliverable has been completed.  It is not a research `PASS`, not a
family reopening, not a PnL result, and not an execution grant.

## Completion-contract audit

| Criterion | Evidence and audit conclusion |
|---|---|
| Accounting | [design.md](design.md) corrects the predecessor's duplicate-slippage equation: `gross_fill` already contains attributed slippage and `net = gross_fill - fees`.  It retains one contract, 100 yen/point, 5 points/tick, 1 tick/side, and 30 yen/side.  The price-free `2,000 → 1,000 → 940` invariant is also in [evidence.md](evidence.md). |
| Stage gates | The design explicitly separates calendar/synthetic work from S1, S2, PnL, OOS, and operations.  It records `capital`, DD, effect, precision, and reopening as not required for preparation; those values remain null for their later stages. |
| Mechanism | Evidence distinguishes Japanese institutional/adjacent research from Korea/US indirect studies, records the already-impounded and reversal alternatives, search strings, failed retrieval attempts, and the absence of a direct 2022–2025 C01-support paper.  No predictive finding was fabricated. |
| Calendar | `calendar_contract.json` has all 1,642 dates, source URLs per row, Cabinet/JPX source hashes, actual final OSE open/not-open exceptions, the 2022-09-23 boundary, and explicit OSE trade-date mapping.  It does not use price or bar presence. |
| Design | Exactly one inherited C01 route and post-boundary, non-overlapping treatment/control definition remain.  Treatment priority handles holiday-plus-weekend closures.  Estimands, fixed costs/stresses, precision method, unknown owner values, and future registration fields are stated without a PnL/count assertion. |
| Verification | The immutable validator is run after artifact generation; its completed output and exit code are appended below.  Source truth is separately reviewed above rather than inferred from a JSON structural pass.  No protected source artifact, config, registry, engine, market input, or validator is changed. |

### Explicit declared-evidence map

The following is the completion-contract declaration requested by the review.
Each key exactly names one sealed criterion.  `satisfied_for_preparation` means
that the bounded document/calendar/synthetic requirement is evidenced; it does
not mean that C01 has predictive support or that a later market stage passes.

| Criterion key | Declared evidence in the permitted artifacts | Direct check and boundary | Declaration |
|---|---|---|---|
| `accounting` | [evidence — Accounting correction](evidence.md#accounting-correction); [design](design.md). | The 2,000 / 1,000 / 60-yen price-free fixture yields 940 yen under `net = gross_fill - fees`; neither artifact subtracts slippage from `gross_fill` again. | `satisfied_for_preparation` |
| `stage_gates` | [evidence — Preparation-stage gates](evidence.md#preparation-stage-gates-and-actual-conclusion); [design](design.md); `calendar_contract.json.stage_gates`. | The JSON booleans are `false`; the documents keep owner values null and distinguish preparation, S1, S2, PnL, OOS, and operation.  No reopening or capital/DD is asserted or required for the completed preparation work. | `satisfied_for_preparation` |
| `mechanism` | [evidence — Mechanism investigation](evidence.md#mechanism-investigation-evidence-counterarguments-and-limits). | Japan-direct material is separated from Korean/US indirect material; already-impounded/null, reversal, and confounding alternatives plus failed retrievals are recorded.  The declaration is explicitly **not** a claim of Japanese cost-after support. | `satisfied_for_preparation` |
| `calendar` | [evidence — Official calendar evidence](evidence.md#official-calendar-evidence-and-versioning); [calendar contract](calendar_contract.json). | Calendar-only audit: exactly 1,642 unique JST dates, all rows have official HTTPS sources, the 2022-09-23 boundary/2023-11-03 exception are retained, and dates with confirmed holiday trading receive the official next-business-day `ose_trade_date`.  No bar presence was used. | `satisfied_for_preparation` |
| `design` | [design](design.md). | One route only; treatment has priority over weekend exclusion.  Unknown future owner values and all unobserved PnL/counts remain null/not evaluated, while the future registered-execution prerequisites are listed rather than created. | `satisfied_for_preparation` |
| `verification` | [review — Fixed validator result](#fixed-validator-result); [evidence — Iteration log](evidence.md#iteration-log). | The frozen validator, coverage/source/whitespace/link audit, and protected-state diff check are recorded with their limits.  Structural success is not represented as validation of market facts or economics. | `satisfied_for_preparation` |

## Actual access and non-results

The task accessed repository documentation/validator and public JPX, Cabinet
Office, research-repository, academic-index, and Federal Reserve source pages.
The official holiday-list XLSX and Cabinet CSV were read in memory solely to
write the calendar metadata; their hashes are preserved in the contract.  No
market prices, volumes, features, trade records, PnL, market-derived counts,
OOS, or Final Holdout were accessed.  No runner, loader, backtest, execution
configuration, grant, registry, manifest, or protected state was changed.

The task makes no claim about continuation, reversal, economics, cohort size,
or suitability for execution.  Its research status is **NOT_EVALUATED**.

## Iteration and verification log

| Iteration | Action / repair | Outcome |
|---|---|---|
| 1 | Inspected policy, governance, closed-family context, predecessor documents, and immutable validator. | Identified the duplicate-slippage formula and the missing official full calendar as repairable preparation items. |
| 2 | Collected official historical Cabinet and JPX calendar evidence; selected final OSE statuses over preliminary announcements; encoded OSE next-business-day trade-date treatment. | Full calendar contract completed with no price input. |
| 3 | Investigated direct/indirect mechanism evidence and counter-hypotheses; documented unavailable source retrievals. | No direct C01 evidence located; limitation recorded, no hypothesis altered. |
| 4 | Generated only the declared calendar contract, wrote the three declared reviews/design artifacts, and ran fixed validation. | Final command result appended below; any failure would require a scoped repair, not a scope expansion. |
| 5 | Re-read first-party JPX rules and cited primary research bodies; added this exact-key evidence map after feedback that the six declarations were not sufficiently explicit. | No substantive design/calendar change was needed: the re-read confirms the recorded institutional boundary and trade-date rule.  Fixed validation is rerun after this documentation repair. |
| 6 | The web text reader could not render the Cabinet CSV/JPX XLSX directly.  Re-fetched each public file only into process memory and recalculated byte count/SHA-256. | Both hashes exactly match `calendar_contract.json.source_snapshot`; the failed display path did not require a fabricated value or a change to the calendar.  No data file was written and no market input was accessed. |
| 7 | The first ad-hoc Markdown-link checker emitted a regular-expression warning.  Replaced it rather than treating its exit code as evidence, then reran link and whitespace checks. | Every local Markdown target resolves and no trailing whitespace was found.  This was a verification repair only; the fixed validator is rerun after the final artifact edit. |

### Fixed validator result

Command completed:

```powershell
.venv/Scripts/python.exe scripts/validate_c01_preparation.py --artifacts docs/strategy/plans/TASK-C01-PREPARATION-02
```

Exit code: **0**.

```json
{
  "passed": true,
  "issues": [],
  "limitations": "Structural/synthetic checks only; Planner must verify source truth, economic reasoning and full scope."
}
```

Independent read-only artifact audit also passed: exactly 1,642 unique dates
from 2021-01-01 through 2025-06-30; 1,099 `cash_open` dates; 32 confirmed OSE
holiday-trading dates; 28 calendar-only treatment-reopen candidates; valid
HTTPS sources for every row; no cash-open/holiday-trading overlap; non-null
assigned trade dates where required; and four retained schedule versions.
All local Markdown links resolve and no declared Markdown artifact has trailing
whitespace.  `git diff --check` exit code was 0.

`git status` showed a pre-existing dirty worktree (orchestration/policy and old
task changes) that this task did not alter.  The only new paths introduced by
this task are its four declared artifacts under
`docs/strategy/plans/TASK-C01-PREPARATION-02/`; the protected configuration,
registry, validator, engine, and sealed predecessor artifacts remain untouched.

## Remaining conditions, intentionally outside this completed task

1. A future authorised S1 may run mapping/causality fixtures against this
   frozen asset; its success is not claimed here.
2. A future registered S2 needs its own complete specification, closure/family
   review as applicable, ReviewReceipt, exact grant, finite remaining budget,
   and non-PnL input authority.
3. Any PnL stage additionally needs owner-set economic minimum/precision and
   capital/DD/operating values, calibrated S2 feasibility, and a separate exact
   registered execution contract.  Grants/budgets currently remain zero.
4. OOS and Final Holdout are not preparation validation and remain unopened.

These are stage-specific future requirements, not blockers for the completed
calendar/document/synthetic preparation scope.
