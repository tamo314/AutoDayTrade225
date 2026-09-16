# C01 S1 calendar labelling and as-of specification

Task: \`TASK-C01-S1-CALENDAR-CAUSALITY-03\`  
Status: calendar-only S1 fixture specification.  It neither evaluates C01 nor
authorises a market-data read, grant, manifest, runner, OOS, or Final Holdout.

## Fixed input and output axis

The sole calendar input is the read-only
\`../TASK-C01-PREPARATION-02/calendar_contract.json\`.  The audit enumerates
every and only its \`cash_open=true\` rows in the covered Development metadata
interval, 2021-01-01 through 2025-06-30 JST.  It reads no price, bar, volume,
order, fill, trade, PnL, or market-derived count.

For a cash-open date \(D\), form \(I(D)\) by walking backward from \(D-1\)
until the preceding cash-open row (or the start of the contract).  The result
is the maximal contiguous list of \`cash_open=false\` calendar dates, in
ascending order.  The classification is deterministic and mutually exclusive:

1. \`TREATMENT_HOLIDAY_REOPEN\`: \(D >= 2022-09-23\) and at least one row in
   \(I(D)\) has \`ose_holiday_trading=true\`.
2. \`ORDINARY_CASH_OPEN_CONTROL\`: \(D >= 2022-09-23\) and \(I(D)\) is empty.
3. \`OUT_OF_REGIME_OR_EXCLUDED\`: every other cash-open date.

Treatment has priority.  Thus a confirmed holiday-trading day followed by a
weekend is one closure interval and creates exactly one treatment reopening;
it is never additionally labelled a weekend exclusion.  A weekend-only
interval, a closure containing no confirmed holiday session, a pre-introduction
date, and an incomplete or contradictory input are excluded.  A calendar date
outside the \`cash_open=true\` output axis is not a C01 cohort date.

### Year-end/New-Year resolution (2026-09-16 repair)

"Year-end/New-Year" is not a fourth exclusion rule and cannot override the
three mutually exclusive rules above.  The required excluded fixture is the
pre-regime reopening on 2022-01-04.  In the post-introduction contract, a
year-end/New-Year reopening is excluded **only** when its maximal preceding
closure has no confirmed \`ose_holiday_trading=true\` row; it is treatment when
such a row is present.  This preserves both required statements without
changing the cohort: treatment priority is evaluated before any descriptive
calendar name.

The sealed contract supplies three post-regime checks of that priority:
2023-01-04 follows 2023-01-03, 2024-01-04 follows 2024-01-03, and 2025-01-06
follows 2025-01-03; each listed predecessor is confirmed holiday trading and
each reopening is \`TREATMENT_HOLIDAY_REOPEN\`.  These are calendar labels,
not market observations.  The corresponding dated notice is retained in the
row's availability record; the later final-list snapshot remains historical
reconciliation only.

\`ose_trade_date\` is checked as calendar metadata only: a cash-open row must
map to itself, and a confirmed holiday row must map to the next cash-open
date.  It is not a bar timestamp and does not grant a market read.

## Availability rule

The label audit keeps two distinct evidence roles for every row:

- \`as_of.schedule_notice\` is the dated JPX implementation/final/half-year
  notice that was published before that decision date for post-introduction
  scheduled status.  The applicable release period is selected only by date:
  2022 implementation (2021-12-24), 2023 H1 (2022-06-30), 2023 H2
  (2022-12-16), 2024 H1 (2023-06-30), 2024 H2 (2023-12-04), and 2025 H1
  (2024-06-28).  It is the available scheduled basis, not a price source.
- \`historical_reconciliation\` is the retained OSE final-list XLSX accessed
  on 2026-09-16.  Its independent publication date is not recorded in the
  frozen contract.  It is used to reconstruct and cross-check historical
  \`ose_holiday_trading\` values, and is explicitly never asserted to have
  been available at an earlier execution decision.

For pre-2022-09-23 rows the availability status is \`OUT_OF_REGIME\`: the
scope boundary is sufficient for exclusion and later final-list information is
not backdated.  Ambiguous or missing input is fail-closed:
\`OUT_OF_REGIME_OR_EXCLUDED\`, no trade date asserted, and no order.

These availability records establish the provenance distinction only.  They do
not establish that an historical operation actually consulted a notice, that a
vendor timestamp agrees with \`ose_trade_date\`, or any causal/economic result.

## Fixed official sources

- https://www.jpx.co.jp/equities/trading/domestic/01.html
- https://www8.cao.go.jp/chosei/shukujitsu/syukujitsu.csv
- https://www.jpx.co.jp/derivatives/rules/holidaytrading/
- https://www.jpx.co.jp/derivatives/rules/holidaytrading/nlsgeu000006hweb-att/List_of_Finalized_Holiday_Trading_Days_J.xlsx
- https://www.jpx.co.jp/news/2040/20211224-01.html
- https://www.jpx.co.jp/news/2040/20220630-01.html
- https://www.jpx.co.jp/news/2040/20221216-02.html
- https://www.jpx.co.jp/rules-participants/rules/revise/aocfb40000001ir7-att/gaiyo.pdf
- https://www.jpx.co.jp/news/2040/20231204-01.html
- https://www.jpx.co.jp/rules-participants/rules/revise/mklp77000000agok-att/gaiyo.pdf

The source URLs and their retained dates/hashes are copied into
\`label_audit.json\`; no network retrieval is performed by this task.
