# C01 F05/F06 exception review

Task: `TASK-C01-EXCEPTION-REVIEW-05`
Current decision: **`NO_EXCEPTION_RECOMMENDED`**

## Scope and non-authorisation

This is a bounded document and metadata review.  It preserves the sealed C01
route, Development `trade_date` range (2021-01-01 through 2025-06-30), S1
labels, and S2 admission gates.  It does not read market data or
market-derived counts, nor price, volume, feature, trade, PnL, OOS, or Final
Holdout information.  It creates no exception, finite attempt, budget,
RunManifest, ReviewReceipt, grant, engine change, or execution permission.

The inherited record remains one C01 hypothesis and zero real-data/PnL
attempts.  F05 and F06 remain `CLOSED_FOR_CURRENT_DEVELOPMENT_SEARCH`, each
with zero remaining economic specifications and parameter variants, and neither
permits automatic reopening.  The unchanged execution setting remains
`market_execution_enabled=false` with zero configured grants.

## Correspondence with preserved records

| Record | Information and regime | Frozen judgment, route, and comparison | Correspondence to C01 |
|---|---|---|---|
| C01 | An official, source-versioned calendar label identifies a post-2022-09-23 cash reopening after a closure interval containing a confirmed OSE index-futures holiday-trading day.  The frozen future decision input is only the OSE DAY 09:00–09:14 direction. | Positive direction means buy, negative means sell, zero/invalid means no order; decide at 09:14, next-eligible 09:15 entry, fixed 10:30 exit, one unit/position.  The calendar-only treatment has the frozen ordinary cash-open comparator. | `NOT_EVALUATED`: S1 validates deterministic labels; S2 specifies only future metadata-only observability and is currently `NOT_GRANTED`.  No market result, economic result, causality finding, or feasibility result exists. |
| R087/Q002 (F06) | Accumulated multi-DAY directional state plus first-15-minute acceptance and causal reference readiness.  The R087 lineage also retains Q001's earlier pre-PnL information insufficiency. | A longer continuation route entered on the next eligible bar after acceptance and exited at 14:55; its paired fade and fixed control comparisons were preserved.  Q002 is a sealed economic `REJECT`; Q001's information insufficiency was not a PnL judgment. | Shared opening-continuation context does not make the records equivalent.  The C01 institutional label neither supplies R087's missing information prerequisites nor repairs its sealed economic result. |
| R101/Q001 (F06) | Frozen two-stage 08:45–08:59 futures and 09:00–09:14 cash-open confirmation, with strict-prior/current-excluded construction. | Decide at 09:14, next 09:15 entry, fixed 10:30 exit, and retain its K/D and fixed comparison structure.  It is a sealed economic `REJECT`; its principal economic AND is not re-scored. | The C01 label defines a special closure cohort but is not direct economic evidence.  It neither reverses R101's economic `REJECT` nor proves that C01 is rejected. |

The calendar difference is therefore a structural cohort definition, not an
economic result.  It cannot automatically solve R101's rejected fixed economic
gates, and it cannot turn R087's earlier information insufficiency into
available C01 observations.  Conversely, the two older outcomes do not decide
the unmeasured C01 route.  Treating either implication as automatic would
confuse institutional-calendar provenance with an economic mechanism.

## Present decision

`NO_EXCEPTION_RECOMMENDED` is the complete decision for this frozen review.
The blockers are all present:

1. F05/F06 are closed with no automatic reopening.
2. No new direct economic evidence for the unchanged C01 route exists in this
   review; the inherited calendar and label audit are not such evidence.
3. No finite S2 attempt or remaining budget exists.
4. No non-PnL RunManifest or ReviewReceipt exists.
5. No exact matching grant exists; grant count is zero.

This is not a conclusion that C01 is economically `REJECT`.  It means C01
remains `NOT_EVALUATED` for economics, causality, market feasibility, and
execution, and the required closed-family exception is not recommended from
the available evidence.

## Finite conditions for reconsideration

Any future reconsideration must be a separate registered review and must
provide all of the following for the unchanged C01 route:

1. New direct economic evidence tied to the C01 holiday-reopen mechanism.
2. A bounded human exception for the closed F05/F06-adjacent route.
3. One finite S2 attempt and an identified remaining budget.
4. A complete non-PnL RunManifest and ReviewReceipt bound to the route,
   output boundary, hashes, range, and attempt.
5. An exact matching grant for that registered task.
6. Reconfirmation of the S1/calendar hashes and the Development
   `trade_date` boundary.

The separate review must recheck every S2 admission gate in order:
`calendar_hash_match`, `development_range_fixed`,
`family_exception_review`, `finite_s2_attempt`, `review_receipt`, and
`matching_grant`.  It must reject before real input access on the first failed
gate.  A change of time, window, threshold, direction, or exit; calendar-only
novelty; or reinterpretation of R087/R101 is not qualifying evidence and does
not form a reconsideration path.

The fixed synthetic cases in `synthetic_exception_cases.json` make this
fail-closed behavior explicit.  `DENY` and
`REQUIRE_SEPARATE_REGISTERED_REVIEW` are not execution permissions.
