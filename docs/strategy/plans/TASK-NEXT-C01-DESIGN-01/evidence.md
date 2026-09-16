# C01 evidence ledger — TASK-NEXT-C01-DESIGN-01

Task date: 2026-09-16 JST
Mode: `design`; this is an evidence and design record, not a RunManifest or
permission to read market inputs.

## Scope and preserved state

This record implements the one frozen task in
[124](../../124_next_research_orchestrator_handoff.md) and
[125](../../125_next_research_initial_task.md).  It was prepared after reading
[AGENTS](../../../../AGENTS.md), the [current policy](../../00_current_research_policy.md),
[governance](../../13_research_governance.md), the [OHLC-limited scope](../../20_225labo_only_scope_and_s2.md),
[finite-search reconciliation](../../120_research_reconciliation_and_finite_search.md),
the [inventory](../../121_research_inventory_index.md), the
[execution-control contract](../../123_registered_execution_control.md), the
[market-calendar specification](../../../infrastructure/05_market_calendar_sessions.md),
the frozen C01 orchestration configuration, and the closed-family registry.

The observed configuration is
`market_execution_enabled=false`, with empty `families`, `batches`, and
`grants` in `config/research_execution.json`.  Registry families F05 and F06
are both `CLOSED_FOR_CURRENT_DEVELOPMENT_SEARCH`, each with
`remaining_economic_specs=0`, `remaining_parameter_variants=0`, and
`automatic_reopen=false`.  All 13 known groups have zero remaining economic
and parameter budget.  This design task neither changes those files nor
allocates an exception.

The working tree was clean before this task.  The only intended changes are the
three required Markdown files in this task directory.  No raw data,
normalised bars, volume, features, trades, price cache, R065 payload, OOS, or
Final Holdout material was opened.  No runner, experiment, orchestrator, or
research CLI was started.

## C01 proposition and its boundary

**C01 is a proposed calendar-conditioned version of an opening-direction
continuation question:** after a known cash-market closure that contains an
OSE holiday-trading day, does the sign of the first 15 minutes after the next
cash reopening continue more often/economically than the same first-15-minute
rule on ordinary cash openings?

It is deliberately not a claim that an observed group difference is a causal
effect of the closure.  Calendar timing can be known beforehand; the first
15-minute direction cannot.  C01 uses no night return, gap, volume, external
price, or position carried across a closure.  The institutional explanation is
that an open derivatives market while the cash market is closed could leave
price discovery or hedging pressure to be incorporated at the next cash open.
That is a mechanism to test, not established predictive evidence.

If the hypothesis is wrong, the treated cohort's direction-following rule will
not have a positive cost-after scheduled-day mean, and its mean will not exceed
the fixed ordinary-opening control at the required precision.  A positive
group difference alone would remain descriptive because holiday dates, calendar
regimes, market volatility, weekday composition, and event frequency are not
randomised.

## Near-family comparison and preserved prior information

| Item | Information, direction, timing, and result | Relation to C01; what is *not* repaired |
|---|---|---|
| F06 / R087-Q001 | [Frozen specification](../../67_r087_q001_multiday_day_trend_acceptance.md) used the previous ten DAY-session returns, a strict-prior 120-day reference, and 09:00--09:14 cash direction; it entered next eligible after 09:14 and exited 14:55.  Its [final result](../../68_r087_q001_multiday_day_trend_acceptance_result.md) was `INCONCLUSIVE` before PnL: MA=147 was below 150 and 2021 EA=0 was below 15. | C01 also conditions a 09:00--09:14 direction but has **no** rolling multi-day price feature or trend-strength cell.  A new calendar label does not cure R087's frozen cells, create its missing observations, or turn its unobserved economics into evidence. |
| F06 / R087-Q002 | Its [result](../../70_r087_q002_multiday_day_trend_acceptance_result.md) is `REJECT`: 129 EA continuation trades, Net −191,740 yen, PF 0.867, and all six frozen main-AND conditions failed. | The Q002 result is known prior information.  C01 must not relabel a calendar condition, window, or threshold as a technical repair of either R087 specification. |
| F06 / R101-Q001 | [R101's specification](../../114_r101_q001_precash_cash_open_confirmation.md) required agreement between 08:45--08:59 futures and 09:00--09:14 cash movements, then entered after 09:14 and exited 10:30.  Its [result](../../115_r101_q001_precash_cash_open_confirmation_result.md) passed its pre-PnL gate but was `REJECT`: 135 A trades, Net −52,600 yen, PF 0.934, and every fixed listed sensitivity was negative. | C01 proposes one cash-opening direction only, selected by a known calendar condition; it does **not** change R101's two-stage selector, thresholds, or economics.  The calendar condition is a possible new explanatory variable, not a rescue of R101's negative result. |
| Other F06 members | The [inventory](../../121_research_inventory_index.md) records R023/R024/R028/R043/R057/R092 and R101 in the cash-open/gap/confirmation family.  Multiple fixed-economic REJECTs and two PnL-free information stops are retained. | The overlap requires an explicit closure review before any future execution.  C01 does not claim a new family merely by changing date eligibility. |
| F05 | The [inventory](../../121_research_inventory_index.md) records night-to-day direction, inventory, and gap studies.  In particular R055/R062/R067 had complete-night/reference-support stops and other F05 members include fixed-economic REJECTs. | C01 does not use a night price, complete night, night gap, or cross-session position, so it does not repeat those input requirements.  However, the institutional story is adjacent to F05 and F05 remains closed.  The absence of a night-price feature is not evidence that the new calendar condition is economically viable. |

`prior_information_seen` for a future C01 review must include the F05/F06
history above, R087's PnL-free stop, R087-Q002's known REJECT, and R101's
known negative result.  `learned_from_failure` is limited to: predeclare a
single calendar condition, keep calendar membership separate from price
availability, preserve unknown filled exits as null, set group-specific
information requirements before PnL, and do not use small time/window changes
to reopen a closed family.

## Old stop reasons versus the new evidence

| Existing fact or stop | Does the C01 calendar fact address it? | Evidence / constraint |
|---|---|---|
| R101's fixed rule had negative cost-after economics. | No. | A JPX institutional rule cannot establish continuation returns or overturn R101's REJECT. |
| R087-Q001 lacked its MA and 2021 cells before PnL. | No. | C01's treated cohort may be much smaller; a new lower gate must not be inferred from R087's shortage. |
| F05 complete-night support was insufficient in several studies. | Not directly applicable, not solved. | C01 intentionally does not consume a night-price feature, but still needs its own calendar and cohort availability audit. |
| The cash market can be closed while an index futures holiday session is available. | Yes, as a **classification** premise only. | JPX primary documents below establish the institutional distinction and its effective boundary. |
| Cash closure predicts a direction or profit after reopening. | No. | No primary or research evidence reviewed here supports this assertion.  It remains the untested C01 hypothesis. |

## Calendar and institutional evidence

The internal [market-calendar specification](../../../infrastructure/05_market_calendar_sessions.md)
requires an exchange calendar rather than inferred bank holidays; it separates
`trade_date` from `calendar_date`, uses `night_calendar_start_date` for night
session versioning, and says a missing calendar is a quality/error condition.
It also records that OSE derivatives holiday trading began in 2022.  Its
current session values are not proof of a complete historical TSE/OSE calendar
for C01.  In particular, the local calendar is generated from source roots
according to the CLI documentation; this task did not open it or use it as a
substitute for a frozen official cash/holiday calendar.

| ID | Primary source, date, access date | Supports | Does **not** support |
|---|---|---|---|
| P01 | [JPX, "2022年におけるデリバティブの祝日取引実施日について"](https://www.jpx.co.jp/news/2040/20211224-01.html), published 2021-12-24; accessed 2026-09-16 JST | OSE/TOCOM planned 2022 holiday trading; the announcement lists 2022-09-23 as the first implementation day and identifies the general holiday scope/exceptions. | Cash-market operating dates, the bar data's calendar mapping, any price continuation, direction, or profitability. |
| P02 | [JPX, "祝日取引"](https://www.jpx.co.jp/derivatives/rules/holidaytrading/), page date not displayed; accessed 2026-09-16 JST | OSE/TOCOM began holiday trading on 2022-09-23; Nikkei 225 futures are eligible; index futures are in scope; holiday sessions use the listed session times and specific trade-date treatment. | A historical complete schedule for 2021--2025H1 without a separately frozen dated calendar; whether every eligible date has source bars; an economic effect. |
| P03 | [JPX, "内国株の売買制度"](https://www.jpx.co.jp/equities/trading/domestic/01.html), page date not displayed; accessed 2026-09-16 JST | TSE domestic-stock trading is closed on weekends, national holidays/holidays, 1--3 January, and 31 December. | That every listed closure is an OSE holiday-trading date, historical cash close/open timestamps, or futures returns. |
| P04 | [JPX, "営業時間・休業日一覧"](https://www.jpx.co.jp/corporate/about-jpx/calendar/), page date not displayed; accessed 2026-09-16 JST | JPX distinguishes TSE cash-market and OSE/TOCOM futures information; an OSE/TOCOM night session can continue to 06:00 even when the following day is a holiday, and directs readers to holiday-trading dates. | A versioned C01 calendar, the past price sample, or a prediction. |

The direct evidence therefore supports a post-2022 institutional eligibility
definition, not application of the current rule to all 2021--2025H1 dates.
An indirect rationale from market microstructure was not promoted to direct
evidence; no primary research paper establishing this C01 prediction was
obtained or relied on.

## Search and access ledger (cumulative)

The frozen external-research allowance is at most eight search queries and
eight primary-source bodies across all calls.  No previous task artifact was
present, so this is the initial cumulative count.

| Type | Actual access | Count used / maximum | Result |
|---|---|---:|---|
| Search query | `site:jpx.co.jp 東京証券取引所 休業日 2025 売買日カレンダー` | 1 / 8 | Located JPX cash-market hours/holiday pages.  No price series was requested. |
| Direct primary-page opens | P01, P02, P03, P04 above | 4 / 8 | Institutional and cash-closure rules confirmed; no predictive study found or needed for this one pass. |
| External time series, purchases, enquiries | None | 0 | Not permitted and not attempted. |

No further external search is required for this task.  The following material
remains deliberately unconfirmed rather than guessed: an official, frozen
2021--2025H1 row-level cash/OSE holiday calendar with publication/effective
version; the exact mapping of each holiday session to OSE `trade_date`; source
bar coverage and as-of availability; treatment/control counts; minimum
economically meaningful effect, capital, allowable drawdown, and operating
minimum; any predictive literature or realized market effect.

## C02--C05 handoff only

| Candidate | Preserved state and evidence location | Action in this task |
|---|---|---|
| C02 VWAP deviation reversion | F11 / R010 and R052 are parked for volume meaning, timing, and continuous-series compatibility; see [120](../../120_research_reconciliation_and_finite_search.md) and [121](../../121_research_inventory_index.md). | No detailed design, input expansion, or search. |
| C03 USD/JPY lag | Requires an external-price scope and an as-of/non-synchronous-input contract. | No data, design, or approval. |
| C04 scheduled macro release continuation | Remains a preliminary F04/F13-adjacent candidate; [124](../../124_next_research_orchestrator_handoff.md) names the BLS archive only as a future entry point. | No query or detailed design. |
| C05 R103 lunch confirmation | R103 was PnL-free because 2021 had 4 events below the frozen 8; see [118](../../118_r103_q001_lunch_cash_confirmation.md) and [119](../../119_r103_q001_lunch_cash_confirmation_result.md). | No gate reduction, rerun, or automatic switch. |

## Evidence conclusion

There is new **institutional classification evidence** for a post-2022
cash-closed/OSE-holiday-trading distinction.  There is no new evidence of
directional continuation, cost-after profitability, suitable cohort size, or
a complete frozen C01 calendar.  The resulting design is recorded in
[design.md](design.md); it remains a provisional S0 proposal and does not
reopen F05/F06.
