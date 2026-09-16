# C01 S2 admission and non-PnL observability specification

Task: `TASK-C01-S2-ADMISSION-04`
Status: **design contract complete; current real-input state is `NOT_GRANTED`.**

## Fixed inheritance and boundary

This contract inherits exactly one frozen C01 representative route, the
Development `trade_date` boundary of 2021-01-01 through 2025-06-30, and S1's
calendar labels.  It changes none of them.  The read-only source identities are:

- C01 preparation calendar SHA-256:
  `6822242ec5f2f7b17bdf7248a13b7099fccb6fa15ded5bd834b90b241d642800`.
- S1 label audit SHA-256:
  `34ca215a726b382714da940b4cc33fbac682d293f411d6803feffd22c7cd94df`.

S2 is only a future availability diagnostic.  If a separately registered task
passes every admission gate, it may determine the existence of the named bars
and emit the allowed metadata below.  It must neither read nor expose bar
values, prices, OHLC, returns, directions, signals, orders, fills, trades,
P&L, performance, or market-derived counts.  This task performs no such
input access.

`data/raw/`, derived bars, price caches, trade records, OOS 2025H2, and Final
Holdout 2026+ are outside the scope.  The registry still records F05 and F06
as closed for the current Development search; this document supplies no
exception.

## Ordered future admission gates

The future registered task must evaluate the following gates in this order and
reject before market input access at the first failure.  All gates are required;
there is no partial admission.

1. **`calendar_hash_match`** — the preparation calendar and S1 label-audit
   SHA-256 values exactly match the values above.
2. **`development_range_fixed`** — the input scope is only the inherited
   Development `trade_date` interval.  Calendar date is retained for reporting,
   but cannot replace `trade_date` as the access boundary.
3. **`family_exception_review`** — human review provides a concrete, bounded
   exception for the F05/F06-adjacent C01 route.  At present it is absent.
4. **`finite_s2_attempt`** — a new registered S2 task specifies its finite
   attempt and remaining budget.  No value is inferred, reserved, or consumed
   here.
5. **`review_receipt`** — that new task has a complete non-PnL RunManifest and
   ReviewReceipt.  They must bind the hashes, Development range, attempt,
   input boundary, and the output schema in this document.  This task creates
   neither artifact.
6. **`matching_grant`** — an exact grant exists for the registered task and
   matches every bound item, including the non-PnL limitation.  A generic,
   expired, changed, or absent grant rejects.

The recorded current state is `NOT_GRANTED`: execution configuration has
`market_execution_enabled=false` and zero configured grants; no family
exception, finite attempt, RunManifest, ReviewReceipt, or matching grant exists
for C01 S2.  A later grant must be checked only by a new registered task; it
does not reopen this completed design task.

## Future S2 input and output contract

The only possible future input purpose is to establish presence or absence of
the four fixed scheduled bars for an already labelled Development date.  The
bar existence check must not retain, calculate with, or emit a bar value.  The
frozen output field allow-list is:

| Field | Meaning and restriction |
|---|---|
| `calendar_date` | Scheduled calendar date from the frozen axis. |
| `trade_date` | Frozen contract trade date; it enforces the Development boundary. |
| `s1_label` | One inherited S1 label; S2 cannot relabel a date. |
| `open_0900_present` | Boolean existence of the required 09:00 bar only; not its open value. |
| `close_0914_present` | Boolean existence of the required 09:14 bar only; not its close value. |
| `entry_0915_present` | Boolean existence of the scheduled 09:15 bar only. |
| `exit_1030_present` | Boolean existence of the scheduled 10:30 bar only. |
| `entry_available` | Boolean: all required pre-entry presence flags are true. It is not a signal, order, or fill. |
| `exit_available` | Boolean: the scheduled 10:30 bar is present. It is not an exit order or fill. |
| `missingness_code` | Ordered list drawn only from `NONE`, `BAR_0900_ABSENT`, `BAR_0914_ABSENT`, `BAR_0915_ABSENT`, and `BAR_1030_ABSENT`; it explains absence without a value. |
| `scheduled_date_count` | Calendar-axis count aggregated only by S1 label. It cannot count observed bars, signals, orders, fills, trades, or outcomes. |

Every other field is prohibited.  At minimum this includes `open`, `high`,
`low`, `close`, `price`, `return`, `direction`, `order`, `fill_price`, `pnl`,
`gross`, and `net`; the matrix also blocks volume, signals, executions,
performance, and market-derived counts.  A requested prohibited field rejects
the whole future S2 request before input access.

The date-level output is limited to the date, label, four presence flags, the
two availability flags, and missingness code.  The only aggregation is the
pre-scheduled calendar-axis count by label.  A missing bar is recorded by its
code and relevant `false` availability flag; it is never converted into a price,
zero return, a direction, an order decision, a fill, or a P&L result.

## Fail-closed decisions

No grant, no F05/F06 exception, a hash mismatch, an out-of-Development range,
or any prohibited output field is a rejection.  The gate sequence does not
permit a caller to repair a failed prerequisite after reading a subset of input.
The synthetic `ACCEPT_SYNTHETIC_ONLY` case is solely a structural witness for
the complete metadata schema.  It has `authorises_real_access=false` and is not
a RunManifest, ReviewReceipt, grant, budget reservation, family reopening, or
permission.

## S2/S3 separation

S2 can at most establish the future non-PnL observability contract.  It cannot
select C01, demonstrate causality, determine economic value, establish sample
adequacy, or demonstrate operational feasibility.  S3 Development PnL, any
economic or causal assessment, and all OOS/Final Holdout work require separate
frozen specifications and authority and are outside this task.  C01 therefore
remains `NOT_EVALUATED` for economics, causality, and execution.
