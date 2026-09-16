# C01 complete preparation design — TASK-C01-PREPARATION-02

Status: **one C01 preparation design; no market run, no PnL, no OOS, and no
execution authorisation.**  This is a corrected preparation record, not a
RunManifest or a request to reopen F05/F06.

## Identity and frozen representative route

| Item | Definition |
|---|---|
| Candidate | C01: continuation of the first cash-opening direction after a cash closure containing an OSE index-futures holiday-trading day. |
| Closest history | F06 is closest and F05 adjacent; both remain closed.  R087/R101 are preserved prior information, not targets for repair. |
| Period | Development calendar metadata only: 2021-01-01 through 2025-06-30 JST.  Treatment/control analysis is post-introduction only; no OOS or Final Holdout access. |
| Initial observation | On a labelled cash-open date, OSE DAY 09:00 open through 09:14 close JST. |
| Direction | `P_d=(close_09:14-open_09:00)/open_09:00`.  If valid and positive, buy; if valid and negative, sell; if zero/invalid, no order. |
| Decision / entry | Decide at 09:14 close; enter one unit at the next eligible normal DAY-bar open (expected 09:15), within the existing ten-minute next-eligible limit. |
| Exit / limits | Signal exit at 10:29 close and execute at the fixed 10:30 DAY-bar open.  At most one entry and one position per date; flat before 09:00 and after exit; no stop, target, re-entry, limit order, or cross-session holding. |

The window, side rule, entry route, exit, cost route, and cohort comparison are
the inherited single representative point.  No holiday subtype, window, side,
weekday/month selection, or exit variation is added by this correction.

## Calendar labels: mutually exclusive and complete

The source-versioned [calendar contract](calendar_contract.json) is the only
calendar input.  `cash_open` describes TSE cash trading; `ose_holiday_trading`
describes a confirmed OSE holiday session for eligible index futures; and
`ose_trade_date` is the assigned OSE day/holiday session trade date.  Those
fields are not inferred from bar availability.

For each cash-open date `D`, let `I(D)` be the maximal, non-empty contiguous
calendar-date interval ending on `D-1` with `cash_open=false`.  Let its start
follow the preceding cash-open date; it may contain a weekday holiday, weekend,
New-Year closure, or their combination.  Labels are assigned before reading
any prices, using this precedence:

| Label | Exact condition |
|---|---|
| `TREATMENT_HOLIDAY_REOPEN` | `D >= 2022-09-23`, `D` is cash open, `I(D)` contains at least one row with `ose_holiday_trading=true`, and `D` is the first cash-open date following that interval.  One interval yields one treatment date. |
| `ORDINARY_CASH_OPEN_CONTROL` | `D >= 2022-09-23`, `D` is cash open, and the immediately preceding calendar date is cash open.  It is an adjacent ordinary weekday opening, not a causal matched counterfactual. |
| `OUT_OF_REGIME_OR_EXCLUDED` | Every other row/date: pre-boundary, non-cash-open day, first cash opening after a closure without confirmed OSE holiday trading, weekend-only reopening, year-end/New-Year reopening, exceptional closure, or a missing/ambiguous calendar field. |

Treatment takes precedence over every other potential label.  Example: the
2022-09-23 holiday session followed by a weekend maps via its OSE trade-date
rule to 2022-09-26; `I(2022-09-26)` contains the true holiday row, so
2022-09-26 is treatment exactly once—not both a long-weekend exclusion and a
treatment.  Conversely, the 2024-08-12 cash closure is not treatment because
the official OSE list says holiday trading was not conducted; the following
cash reopening is excluded.  A confirmed closed-date interval cannot become a
control merely because it includes a weekend.

`scheduled_axis` records every cash-open date in the contract, plus its label
or exclusion reason before price access.  The future `E_exec(D,09:14)` contains
the frozen calendar version/label and only the valid 09:00/09:14 data available
at the decision.  `E_analysis` may add entry/fill/exit observability and
accounting after the decision, but cannot relabel dates.  A known no-signal or
pre-fill cancellation is zero for that scheduled cohort date; a filled position
with unknown fixed exit is `null`, never zero.

## Correct accounting and synthetic invariant

The correct accounting is:

```text
reference_gross = signed_reference_price_change × 100 yen/point × 1 contract
gross_fill      = reference_gross − slippage_attribution
net             = gross_fill − fees
```

Base costs are exactly one 5-point tick plus 30 yen fee **per side**.  Thus the
two-sided slippage attribution is 1,000 yen and two-sided fees are 60 yen; the
round-trip base cost is 1,060 yen.  No other cost is registered.  In the
synthetic check `reference_gross=2,000`, `slippage_attribution=1,000`, and
`fees=60`; hence `gross_fill=1,000` and `net=940 yen`.  Slippage is not
subtracted again after `gross_fill`.

## Planned estimands, controls, and fixed stresses

The planned primary estimand—if a later registered PnL stage becomes
authorised—is scheduled-date, cost-after daily PnL over all treatment dates:

`theta_T = sum(PnL_D on scheduled treatment dates) / count(scheduled treatment dates)`.

The fixed co-primary cohort difference uses the identical route/costs on
ordinary controls:

`theta_C = sum(PnL_D on scheduled control dates) / count(scheduled control dates)`

`Delta_calendar = theta_T - theta_C`.

It includes signal frequency, cancellations, and costs, so it is a rule/cohort
difference—not proof that a holiday closure caused an effect.  Reported later
would include total Net, PF, trade rate, side counts, max drawdown, and unknown
count separately; conditional yen-per-filled-trade cannot replace the scheduled
date estimand.

| Class | Frozen item | Purpose |
|---|---|---|
| Base | 1 tick/side + 30 yen/side, same representative route | Primary cost-after route. |
| Mandatory stress | 2 ticks/side, same route | Cost tolerance. |
| Mandatory stress | 3 ticks/side, same route | Cost tolerance. |
| Mandatory stress | Doubled fee, same route | Fee tolerance. |
| Mandatory stress | One additional eligible-bar entry delay and fixed 10:30 exit | Delay tolerance without longer holding. |
| Descriptive only | Calendar version/counts, closure duration, side balance, initial absolute move, entry cancellation, unknown exit, concentration | Mechanism/quality reporting; never route selection. |

No zero-cost report, changed observation/exit, new holiday subtype, or
weekday/month split is a rescue path.

## Precision and sample-size design—method, not fabricated values

The following owner-controlled values remain unknown and must remain `null`:
`minimum_economically_meaningful_effect`, `required_precision`,
`n_treatment_min`, `n_control_min`, `capital`, `allowable_drawdown`, and
`operating_minimum`.  Calendar preparation and synthetic validation do not need
them.  They are required only before a later PnL/economic decision; no historic
threshold, common 800-event rule, or observed calendar count may substitute.

Once an owner specifies a minimum effect `delta_min` in yen per scheduled date
and a required precision, the frozen design method is:

1. Obtain no-PnL S2 label/fill/missingness counts under a separately registered
   manifest; this must not emit returns or PnL.
2. Use the scheduled cash-date axis and the predeclared 20-trade-date,
   non-wrapping moving-block bootstrap (10,000 draws, seed `20260916`,
   linear-percentile 95% intervals).  Recompute each cohort denominator in a
   resample; a zero denominator is `null`.
3. With only authorised synthetic daily-PnL generators, calibrate Type-I error
   at `Delta_calendar=0` and power at the owner value `delta_min`, preserving
   observed/assumed rarity, cancellation, side imbalance, heavy tails,
   dependence blocks, and null exits.  Report Monte-Carlo uncertainty.  The
   governance targets are false-positive rate at most 5% and power at least 80%.
4. Translate the required observed treatment/control scheduled-date counts into
   a feasibility conclusion without changing C01.  Empty cohorts, unknown
   exits, a null owner value, or failed calibration stop before PnL; they do not
   authorise tuning.

This task performs none of those count, calibration, or PnL calculations.

## Stage gates: separated by authority and purpose

| Stage | Minimum input / output | Does it require capital/DD or family reopening? | Current result |
|---|---|---|---|
| Preparation calendar + fixed synthetic validation | Official calendar sources, corrected accounting, contract structure; no prices | **No / No.** | Complete here. |
| Future S1 mapping/causality fixtures | Frozen contract plus separate authorisation; must prove label priority, pre-boundary exclusion, OSE trade-date mapping, causality and null-exit handling without real results | No / No for the fixture itself. | Not run. |
| Future S2 availability | Registered, matching non-PnL manifest/review receipt/grant/budget; allowed Development inputs only; emits observability/count diagnostics only | Not for the diagnostic itself; closure review and full specification still gate its authorisation. | Not authorised. |
| Future Development PnL | Complete frozen S0, owner economic/precision values, S1/S2 success, closure-family exception, finite slot, matching registered grant and budget | **Yes / Yes**, plus all listed requirements. | Not authorised; zero grants/budget. |
| OOS then operations | Separate frozen candidate and opening procedure after Development decision; operational risk/capital/DD values and operating controls | **Yes / N/A**; never inferred from preparation. | Not authorised. |

For a later economic analysis, the required lower bounds for `theta_T` and
`Delta_calendar` remain unset until the owner values and calibration are
reviewed.  A negative, positive, or unavailable later result cannot cause a
calendar, direction, or route revision under this preparation design.

## Future registration contract (not created)

Before any real-data stage, a new bounded/registered task must supply a complete
immutable specification snapshot and hash, exact `calendar_contract.json` hash,
stage (`S1`, `S2`, or PnL), permitted Development inputs/columns, output path,
seed, fixed conditions, finite attempt identity and remaining budget,
ReviewReceipt, and a grant that exactly matches all of those fields.  It must
use `research execute`; this task creates no manifest, receipt, grant, or
budget.  OOS 2025H2 and Final Holdout 2026+ remain outside the contract.
