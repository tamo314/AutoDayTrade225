# C01 provisional S0 design — TASK-NEXT-C01-DESIGN-01

Status: **one provisional design only; not freezeable or executable**. No
market-derived count, return, PnL, simulation result, or test result is
reported here. This is not a RunManifest, grant, implementation request, or
exception to the closed F05/F06 families.

## 1. Identity and known information

| Field | Provisional definition |
|---|---|
| Candidate | C01: continuation of the first cash-opening direction after a cash-market closure containing an OSE holiday-trading day. |
| Parent / near families | F06 (cash-open gap/confirmation) is closest; F05 (night-to-day direction/inventory/gap) is adjacent. Both remain closed. |
| `prior_information_seen` | Yes: F05/F06 history, R087-Q001 PnL-free `INCONCLUSIVE`, R087-Q002 `REJECT`, R101-Q001 `REJECT` (Net −52,600 yen) and its negative fixed sensitivities. |
| `learned_from_failure` | Do not use a changed window, side, cutoff, or exit to repair a closed rule. Predeclare a calendar label, preserve unknown filled exits as null, and set C01's own information/calibration requirements before PnL. |
| Hypothesis | A cash closure during which eligible derivatives can trade may leave a different incorporation process at the next cash open. This is an observational mechanism to test, not a causal or profitability claim. |
| Falsification expectation | If wrong, the treated route will not have a positive cost-after scheduled-day mean and/or will not exceed the fixed ordinary-opening control at the required precision. |

The direct institutional selector evidence and the absence of directional or
profit evidence are in [evidence.md](evidence.md). C01 differs from R087 by
having no multi-DAY return or rolling price-strength selector. It differs from
R101 by having no 08:45--08:59 confirmation; the only pre-price selector is a
calendar label. Neither difference establishes independence from F06 or rescues
R101's negative result.

## 2. Required calendar definition (JST)

### Frozen asset required before S1

`calendar_version = null` and `calendar_hash = null`. This task did not find or
create a frozen, official 2021-01-01--2025-06-30 cash/OSE holiday calendar.
Before S1, one source-cited, read-only calendar asset—not source-bar presence—
must contain at least:

`cash_calendar_date`, `cash_market_open`, `cash_open_jst`, `cash_close_jst`,
`ose_trade_date`, `ose_day_session_scheduled`,
`ose_holiday_trading_scheduled`, `night_calendar_start_date`,
`schedule_version`, `source_url`, `source_publication_date`, `effective_from`,
`effective_to`, `calendar_version`, and `sha256`.

`cash_calendar_date` is the TSE cash calendar. `ose_trade_date` is the OSE
trade date for the DAY session and must be mapped from the calendar, not
assumed equal to a midnight date. `night_calendar_start_date` is retained for
mapping audit only, never as a price feature. The asset must preserve the
2022-09-23 holiday-trading introduction and the post-2024 OSE session regime as
separate effective schedule versions. The [calendar specification](../../../infrastructure/05_market_calendar_sessions.md)
requires these distinctions but is not that missing complete asset.

### Closure interval and cohort labels

For a scheduled cash reopening date `d`, define the cash-closure interval as
the period from the official `cash_close_jst` of the immediately preceding
scheduled cash-open date through the official `cash_open_jst` on `d`. C01
neither computes a price return across this interval nor holds a position over
it.

Exactly one label must be assigned from the frozen calendar before prices are
read:

| Label | Frozen condition |
|---|---|
| `TREATMENT_HOLIDAY_REOPEN` | `d` is a scheduled cash-open date on/after the holiday-trading boundary; the preceding cash-closure interval contains at least one scheduled cash-closed weekday marked `ose_holiday_trading_scheduled=true`; `d` is the next scheduled cash open after that interval. |
| `ORDINARY_CASH_OPEN_CONTROL` | `d` is on/after that boundary; the preceding interval has no scheduled cash-closed weekday, OSE holiday-trading date, Saturday/Sunday, or year-end/new-year closure. It is an ordinary adjacent-weekday opening, not a matched causal counterfactual. |
| `OUT_OF_REGIME_OR_EXCLUDED` | Pre-2022-09-23 dates, weekends, closures without scheduled OSE holiday trading, multi-day year-end/new-year closures, exceptional closures, or a missing/ambiguous calendar field. |

Thus the treatment is neither a generic long weekend nor every cash holiday.
Weekday nights between ordinary cash sessions are controls only; weekends and
year-end/new-year are excluded rather than creating another condition. Dates
before the policy boundary remain out-of-regime metadata, so the current
institutional arrangement is never imposed on 2021--2022. A missing calendar
field means calendar-unavailable, not a no-trade inferred from missing bars.
The label must have been published/known by the preceding cash close; a later
calendar correction is a new version and cannot rewrite original `E_exec`.

## 3. One representative route

There is exactly one representative point. No calendar subtype, weekday/month,
side, observation-window, or exit search is permitted.

| Element | Frozen representative definition |
|---|---|
| Initial observation | On either primary cohort date, use OSE DAY bars from 09:00 open through 09:14 close JST. |
| Direction | `P_d=(close_09:14-open_09:00)/open_09:00`. If either required bar is unavailable/invalid or `P_d=0`, no order. Otherwise `side_d=sign(P_d)` (positive long, negative short). |
| Decision | 09:14 close JST after the initial direction is observed. |
| Entry | One unit at the next eligible normal DAY-bar open, expected 09:15. The existing next-eligible limit is ten minutes; no eligible bar within it is a pre-fill cancellation with zero cost/PnL. |
| Exit | Fixed 10:30 JST DAY-bar open (10:29 close signal). Entry delay does not extend the exit. |
| Limits | One entry attempt and at most one position per `d`; flat before 09:00 and after same-day exit; no Stop, Target, re-entry, limit order, or closure/session-spanning position. |

The shared 09:00--09:14 / 09:15 / 10:30 route enables comparison with the cash
portion of R101, while C01 changes only the pre-known calendar selector. It
does not alter R101's frozen two-stage specification.

## 4. Information sets and missingness

| Object | Definition |
|---|---|
| `scheduled_axis` | Every scheduled cash-open date from 2021-01-01 through 2025-06-30 in the frozen cash/OSE calendar, annotated before price access with one §2 cohort. Primary/control estimands use their fixed label subsets; excluded dates remain recorded. |
| `U` | `null`: C01 has no rolling threshold, learned quantile, fitted calendar effect, or prior-price reference. A previously published calendar is schedule metadata, not `U`. |
| `E_exec(d, 09:14)` | The frozen label/version and only valid 09:00 open/09:14 close through decision time. It excludes entry/fill/exit status, later volatility, and outcome-based cohort changes. |
| `E_analysis` | Scheduled label, observation status, entry/fill status, fixed-exit observability, and post-decision accounting. It may report an unknown exit but cannot change the prior order. |

A no-signal or pre-fill cancellation is zero on the relevant scheduled cohort
date. A filled position with unknown fixed exit has PnL `null`, never zero. Any
such unknown blocks complete primary Net, PF, CI, and promotion; only a clearly
labelled partial observed total could be descriptive.

## 5. Economic, mechanism, and robustness plan

### Primary economic estimand

The primary estimand is the cost-after mean daily PnL over every treatment
scheduled date, with zero for known no-trade/pre-fill cancellation and null for
unknown filled exit:

`theta_T = sum(PnL_d on treatment dates) / count(treatment scheduled dates)`.

Report total Net, PF, trade rate, side counts, maximum drawdown, and unknown
count separately. Do not replace it with a conditional yen-per-filled-trade
result. The base cost is one unit, 5-point tick, 100-yen multiplier, one tick
plus 30 yen per side (1,060 yen round-trip in the existing arithmetic).
`net = gross_fill - slippage_attribution - fees`; slippage is never deducted
twice.

Provisional conditions are `Net > 0`, `PF > 1`, a predeclared CI lower bound
for `theta_T > 0`, and no unknown filled exits. They are not operating approval.

`minimum_economically_meaningful_effect = null`, `required_precision = null`,
`n_treatment_min = null`, `n_control_min = null`, `capital = null`,
`allowable_drawdown = null`, and `operating_minimum = null`. These have not
been supplied by the owner and cannot be invented from R087/R101 gates, a
common 800-event rule, or an arbitrary yen threshold. This is why the S0 is not
freezeable even though its route is specified.

### Co-primary calendar-cohort comparison

Apply the same direction, entry, exit, and cost route to the fixed ordinary
control cohort:

`theta_C = sum(PnL_d on control dates) / count(control scheduled dates)`

`Delta_calendar = theta_T - theta_C`.

This is a cohort rule difference that includes signal frequency and costs, not
a causal treatment effect. Required alternative-explanation diagnostics are
cohort frequency, first-15-minute absolute-move distribution, schedule version,
weekday/month distribution, side balance, entry-cancellation rate, and
missing-exit rate. They cannot select a different route.

For both estimands, use the full scheduled cash-date sequence in a 20-trade-date
non-wrapping moving-block bootstrap, 10,000 repetitions, tail truncation,
linear-percentile 95% intervals, seed `20260916`. Recompute labelled-cohort
denominators in each resample; a zero denominator yields null rather than a
successful-subset reweight. The inference unit is scheduled cash date and the
block approximation does not correct the prior C01/family choice. The required
lower bound for `Delta_calendar` stays null until the economic minimum and
calibration are reviewed; it may not be demoted after results.

| Class | Frozen item | Purpose |
|---|---|---|
| Mandatory stress | Two ticks per side; same route | Cost tolerance. |
| Mandatory stress | Three ticks per side; same route | Cost tolerance. |
| Mandatory stress | Fee doubled; same route | Fee tolerance. |
| Mandatory stress | One extra eligible-bar entry delay; fixed 10:30 exit | Delay tolerance without a longer hold. |
| Descriptive only | Calendar counts/version, closure-duration class, signal sides, entry/exit observability, absolute initial move, concentration/top-trade summaries | Mechanism/support/reporting only; no reselection. |

No zero-tick result, different observation window/exit, holiday subtype, or
weekday/month split is an additional sensitivity or rescue path.

## 6. Future S1 audit contract (not run)

S1 needs distinct authorization, a source-cited frozen calendar, and a
read-only synthetic audit. It must prove with fixtures that: (a) a 2021 cash
holiday is excluded, (b) a post-boundary OSE holiday-trading closure maps to
one next-cash-open treatment date, (c) an ordinary adjacent weekday is control,
(d) weekend/year-end dates are excluded, (e) cash-calendar date, OSE trade
date, and night start date are not conflated, (f) the 2024 regime version is
preserved, and (g) 09:14 decision / next-eligible entry / null unknown-exit
rules are causal. It must not create PnL or real cohort counts.

S1 stops `INFORMATION_INSUFFICIENT` if a required official calendar
field/version is absent, a label derives from source-bar presence, the 2022
boundary cannot be mapped, or any causality/unknown-exit synthetic fixture
fails. It does not pass S2.

## 7. Future S2 non-PnL and calibration contract (not run)

S2 needs a separate registered authorization and may read only permitted
Development inputs. It may use the frozen calendar and the minimum OHLC/quality
fields to assess 09:00/09:14 signal and entry/exit observability, but must not
return a price return, gross/net PnL, win/loss, PF, rank, or cost-after result.
Its output is limited to scheduled-label/exclusion counts, valid initial-window
counts, non-zero signal counts by side, entry attempts/cancellations/fills,
fixed-exit observability, filled-but-unknown-exit counts, and cash-date/
`trade_date` mapping checks. Every economics/mechanism/robustness field remains
`NOT_EVALUATED`.

S2 stops before PnL if a required calendar field is missing, either cohort is
empty, any required minimum remains null, a filled exit can be unknown, or
calibration fails. A later shortfall is `INCONCLUSIVE`/`HOLD`, never authority
to alter the closure, direction, period, or control.

Synthetic calibration (also separately authorized) uses a cost-consistent
daily-PnL generator with 20-date dependence blocks, rare-event frequency, side
imbalance, heavy tails/noise, cancellations, and null exits. It estimates type
I error at `delta=0` and power at owner-specified `delta_min`, in 10,000
repetitions with base seed `20260916` and deterministic replicate seeds. It
must report Monte-Carlo uncertainty; the targets from
[13](../../13_research_governance.md) are false-positive rate <=5% and power
>=80%. It must detect zero treatment events, imbalance, null-to-zero
misclassification, and future-price-derived calendar labels. `delta_min` and
precision remain missing, so no calibration calculation or PASS is claimed.
At most two documented revisions of this same design are allowed; a failure
ends in HOLD/CLOSE, never real-PnL tuning.

## 8. Required conditions after this document

Before a non-PnL S2, the project would need new frozen official calendar
evidence, a closure/family exception review, owner-specified effect/precision/
capital/DD/operating values (or a decision to close), a complete S0 snapshot,
S1 synthetic audit, and a registered S2 manifest, ReviewReceipt, matching
grant, and real remaining budget. Current grants and budgets are zero. This
document creates none of them.
