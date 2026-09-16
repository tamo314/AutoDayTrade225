# C01 preparation evidence — TASK-C01-PREPARATION-02

Status: **preparation completed; C01 remains unevaluated and is not authorised
for market access.**  This artifact corrects and supersedes only the preparation
description in the read-only `TASK-NEXT-C01-DESIGN-01` artifacts.  It does not
alter any sealed result, registry entry, strategy, grant, manifest, or execution
configuration.

## Scope, inherited record, and access ledger

The sole proposition is C01: after a cash-market closure that contains a
scheduled OSE index-futures holiday-trading day, the sign observed from 09:00
through 09:14 JST on the next cash opening may continue under the one fixed
09:15-entry/10:30-exit route.  This is an observational question, not a claim
of causation, profitability, sufficient sample size, or an exception for the
closed F05/F06 families.

The read-only predecessor recorded one Executor call, one Planner call, one web
query, four primary-source bodies, zero PnL accesses, and zero real-data
attempts.  This task retains one economic hypothesis and zero real-data/PnL
attempts.  It adds no strategy variant, window, direction, control, cost, or
period.

| Activity ledger, cumulative through this task | Count / result |
|---|---|
| Inherited formal search record | 1 query; 4 primary-source bodies; former 8-item limit no longer applies to this autonomous preparation task. |
| This task's public-source discovery | 4 batched searches containing 16 query strings; 14 public source bodies/resources materially inspected, including two source files read in memory. |
| Repair-audit public-source work | 1 additional batched search containing 4 query strings; 5 source-body review events (JPX holiday rules, TSE cash rules, the 2023-H2 final notice, the JPX working paper, and the Federal Reserve working paper), plus 2 in-memory official-file byte/hash rechecks (Cabinet CSV and JPX final-list XLSX). Five in-document finds navigated two already-counted bodies and are not counted as new source bodies. |
| Cumulative activity count | 21 query strings and 25 source-body review events under the inherited counting convention.  This is a record, not a remaining research or PnL budget. |
| Market-data access | **0**. No raw/normalised bars, OHLC, volume, features, cache, order/trade record, R065 payload, OOS, or Final Holdout was opened. |
| Execution access | **0**. No research runner, backtest, loader, manifest, grant, registry, protected configuration, or orchestrator was opened or changed. |
| External actions | No purchase, account use, or enquiry.  Public HTTPS documents were read; the two official calendar files were fetched into process memory only to calculate a metadata-only calendar, then discarded. |

The old F05/F06 history remains relevant: F06 contains the PnL-free stop for
R087-Q001, the known R087-Q002 rejection, and R101-Q001's fixed cost-after
rejection; F05 remains an adjacent, closed night-to-day family.  A calendar
classification is neither a repair of those outcomes nor an execution
exception.  See the preserved predecessor [evidence](../TASK-NEXT-C01-DESIGN-01/evidence.md)
and the [inventory](../../121_research_inventory_index.md).

## Accounting correction

The predecessor's line `net = gross_fill - slippage_attribution - fees` was
wrong because `gross_fill` already incorporates the attributed execution
slippage.  The governing definitions in [13](../../13_research_governance.md)
are now used exactly:

```text
reference_gross = signed_reference_price_change × multiplier_yen × qty
gross_fill      = reference_gross − slippage_attribution
net             = gross_fill − fees
```

There are no other registered costs in this preparation asset.  With one
contract, a 5-point tick, a 100-yen point multiplier, one tick per side, and a
30-yen fee per side, round-trip attributed slippage is
`2 × 5 × 100 = 1,000 yen` and fees are `2 × 30 = 60 yen`.  For the price-free
synthetic example `reference_gross=2,000`, `slippage_attribution=1,000`, and
`fees=60`, `gross_fill=1,000` and `net=940 yen`.  It is an arithmetic invariant,
not a market result.  The correction preserves the established 1,060-yen
round-trip deduction and prevents the former extra 1,000-yen charge.

## Official calendar evidence and versioning

`calendar_contract.json` maps every JST calendar date from 2021-01-01 through
2025-06-30.  It is constructed from explicit rule sources, not bar presence,
bank calendars, or a current session template projected backwards.

| ID | Official source and observed version | What it supports | What it does not support |
|---|---|---|---|
| CAL-1 | [TSE domestic-equity trading days](https://www.jpx.co.jp/equities/trading/domestic/01.html), page accessed 2026-09-16 JST | TSE is closed weekends, national holidays/holidays, 1–3 January, and 31 December. | A futures holiday session, a price bar, or PnL. |
| CAL-2 | [Cabinet Office historical holiday CSV](https://www8.cao.go.jp/chosei/shukujitsu/syukujitsu.csv), accessed 2026-09-16 JST, 21,538 bytes, SHA-256 `cec37a743c96995cdb9cb52b685c9003634682a9b0e1a640a6b9b96881fe964a` | The actual national-holiday and substitute-holiday dates through the target interval, including the exceptional 2021 Olympic moves. | Exchange-specific OSE opening or trade date. |
| CAL-3 | [OSE final holiday-trading list](https://www.jpx.co.jp/derivatives/rules/holidaytrading/nlsgeu000006hweb-att/List_of_Finalized_Holiday_Trading_Days_J.xlsx), accessed 2026-09-16 JST, 11,567 bytes, SHA-256 `93daa202fe71cff97a7b3691d793a40d101ba4058c8dffe03550b6ee2ca29721` | Exact historical OSE holiday candidate days and final open/not-open status from the 2022-09-23 introduction through 2025H1. | Cash-market price behaviour or a separate holiday OHLC series. |
| CAL-4 | [OSE holiday-trading rules](https://www.jpx.co.jp/derivatives/rules/holidaytrading/), page accessed 2026-09-16 JST | Index futures are eligible; holiday trading began 2022-09-23; its day is assigned the following weekday's trading day, together with the preceding weekday night session and subsequent weekday day session. | A return forecast, or proof that a vendor labels midnight dates the same way. |
| CAL-5 | [2022 implementation notice](https://www.jpx.co.jp/news/2040/20211224-01.html), published 2021-12-24 | The four 2022 implementation dates and the initial 2023 New-Year treatment, with 2022-09-23 as first implementation. | A rule before its effective date. |
| CAL-6 | [2023 H2 final notice](https://www.jpx.co.jp/news/2040/20221216-02.html), published 2022-12-16 | The final revision that made 2023-11-03 non-trading, despite the earlier annual plan. | Any inference from a preliminary schedule. |
| CAL-7 | [2024 H1 rule amendment](https://www.jpx.co.jp/rules-participants/rules/revise/aocfb40000001ir7-att/gaiyo.pdf), published 2023-06-30, and [2025 H1 rule amendment](https://www.jpx.co.jp/rules-participants/rules/revise/mklp77000000agok-att/gaiyo.pdf), published 2024-06-28 | Dated examples of the enacted half-year holiday lists, including exclusions and 2025H1 dates. | That the preliminary 2024 schedule was final. |
| CAL-8 | [J-GATE3.0 go-live](https://www.jpx.co.jp/corporate/news/news-releases/0060/20210921-01.html), published 2021-09-21, and [2024-11-05 trading-hours change](https://www.jpx.co.jp/corporate/news/news-releases/0060/20241009-01.html), published 2024-10-09 | The 2021-09-21 and 2024-11-05 schedule boundaries recorded as `schedule_version` in the contract. | Any alteration to C01's 09:00–10:30 morning route or a price-based calendar label. |

The aggregate final-list file does not display an independent publication date
in the retrieved workbook.  Rather than invent one, the contract records its
URL, access date, byte length, and content hash, while the dated notices above
preserve the relevant publication/effective history.  Its final statuses capture
the material exceptions: 2023-01-09 and 2023-11-03; 2024-01-08, 2024-08-12,
2024-09-16, and 2024-11-04; and 2025-01-13 are cash-closed but not OSE
holiday-trading dates.  This is why a statutory-holiday formula alone is not
used.

`ose_trade_date` is deliberately narrow: it denotes the scheduled OSE day/
holiday session's assigned trading day, not a vendor timestamp nor the
post-midnight tail of a prior night session.  A regular cash-open day maps to
itself.  A confirmed holiday-trading day maps to its next TSE cash-open date;
all other cash-closed dates are `null`.  Thus 2022-09-23 maps to 2022-09-26,
and a holiday plus the following weekend remains one pre-open interval.  The
contract stores no night-calendar-start field because C01 neither reads nor
uses night prices; a later runner must retain that field separately for a
timestamp audit.

## Mechanism investigation: evidence, counterarguments, and limits

The following research was deliberately separated into Japan-direct evidence
and indirect settings.  It establishes neither a C01 return sign nor a usable
sample size.

### Direct Japanese/institutional evidence

| Source | Finding that may motivate investigation | Boundary and counterweight |
|---|---|---|
| [JPX/OSE, Miyazaki (2016), *Analysis of Differences in Trading Behavior at Day and Night Sessions for Nikkei 225 Futures*](https://www.jpx.co.jp/english/corporate/research-study/working-paper/b5b4pj000000i468-att/E_Summary_JPX_working_paper_No14.pdf) | Using OSE Nikkei 225 Futures order data for July–August 2014, the study reports different night-session order-book/order-aggressiveness relationships and says the order book contributes more to price discovery by order in that sample. | It predates 2022 holiday trading, uses regular trading sessions, and explicitly distinguishes its narrow order-level contribution from the market-wide price-discovery mechanism.  It contains no holiday/reopen continuation test. |
| [Covrig, Ding & Low (2004), repository record and abstract](https://ink.library.smu.edu.sg/lkcsb_research/731/) | The author-provided conference-paper abstract describes intraday TSE/OSE/SGX Nikkei 225 price-discovery analysis and reports a large futures contribution in its historical sample. | The full document is unavailable on the repository, its 2004 market structure predates the C01 regime, and it does not test holidays or a 09:00–09:14 continuation rule.  It is contextual, not support. |
| CAL-4 and CAL-5 | OSE made index futures available while cash is closed and explicitly groups holiday activity with the preceding night and next weekday trading day. | Institutional availability does not show that unincorporated information remains at the cash opening; JPX does not publish holiday-only OHLC/volume separately, so these documents cannot supply an empirical C01 mechanism result. |

### Indirect evidence and competing explanations

| Source | What it says | Consequence for C01 |
|---|---|---|
| [Joo, Seon & Lee (2016), KOSPI 200](https://www.kci.go.kr/kciportal/ci/sereArticleSearch/ciSereArtiView.kci?sereArticleSearchBean.artiId=ART002116586) | In a Korean 2009–2011 sample, the authors report that overnight futures trading contributed to price discovery while cash was closed. | This supplies an **indirect** mechanism possibility only.  Different exchange, dates, contract, and overnight rather than Japanese holiday regime prevent extrapolating a sign or statistical result. |
| [Grant, Wolf & Yu (2005), US index futures](https://www.sciencedirect.com/science/article/pii/S0378426604000949) | The abstract reports short early continuation followed by intraday reversal after large opening changes in US index futures. | Reversal/overreaction is a live alternative to continuation, but the setting, event definition, and sample differ.  It is not evidence to change C01's frozen 15-minute direction or 10:30 exit. |
| [Andersen et al. (2006), Federal Reserve working paper](https://www.federalreserve.gov/pubs/ifdp/2006/871/ifdp871.htm) | The study discusses futures markets trading when cash is shut and real-time incorporation of macro news in other markets. | It supports the opposite possibility: information may already be incorporated in futures, leaving no predictable post-open continuation.  It is not Japanese holiday evidence. |

The direct testable mechanisms remain mutually competing rather than assumed:

1. **Continuation:** cash opening orders and component-stock price formation may
   continue a directional imbalance after a special closure.
2. **Already impounded / null:** if holiday futures efficiently aggregate the
   relevant information, the cash opening should absorb it without a persistent
   09:14-to-10:30 direction.
3. **Reversal:** opening-auction liquidity, temporary imbalance, or a stale
   futures/cash basis may overshoot and then reverse.
4. **Calendar confounding:** weekday, month, closure length, global events, and
   the post-2022 regime can change both cohort membership and outcomes without
   a holiday-session mechanism.

The future fixed diagnostics (not run here) therefore retain label frequency,
closure duration, weekday/month composition, side balance, initial absolute
move, entry cancellation, and missing exit rate.  They are explanatory checks,
not parameters for selecting a different strategy.  There is **no located
Japanese 2022–2025 study directly testing C01, and no located source supporting
cost-after 09:14-to-10:30 continuation.**

Search strings used included `Nikkei 225 futures cash market price discovery`,
`Nikkei 225 futures price discovery`, `futures market open cash market closed
price discovery`, `holiday trading futures market price discovery cash market
reopening`, `opening auction price reversal`, and JPX-year/holiday queries for
2023–2025.  Attempts to open the MDPI Nikkei spot–futures paper DOI and the
Nagasaki University paper PDF through the text reader failed with a retrieval
error; they are not used for a substantive finding.  The current JPX historical
XLSX was not text-renderable in the browser, so it was instead read from its
official HTTPS URL in memory and its exact date/status cells and SHA-256 were
checked.  No paywall bypass, market-data substitution, or external request was
attempted.

## Preparation-stage gates and actual conclusion

| Stage | Needed here? | Status / reason |
|---|---|---|
| Calendar asset and accounting synthetic invariant | Yes | Completed in the declared contract and fixed validator; neither requires capital/DD nor a family reopening. |
| S1 calendar/causality fixtures | Future, separately authorised | Not run.  The fixed validator proves the required accounting and label-priority invariants only; it is not an S1 market audit. |
| S2 non-PnL availability | Future, registered execution only | Not run; needs a full frozen S0, an authorised manifest/review receipt/matching grant and actual remaining budget. |
| Development PnL | Future, separate registered execution | Not authorised.  The closed-family exception review, finite slot, full specification, owner economic/precision values, and matching grant remain required. |
| OOS / operation | Later distinct stages | Not authorised; no OOS or Final Holdout access occurred.  Capital, allowable DD, and operating minimum remain `null` until owner-provided. |

The preparation task can therefore be complete without a research `PASS`.
Research judgement remains **NOT_EVALUATED**: no directional, economic, count,
or predictive conclusion is made.  The only completed outcome is a source
versioned calendar-and-design preparation package and its synthetic structural
validation.

## Iteration log

| Iteration | Actual action and repair | Result |
|---|---|---|
| 1 — 2026-09-16 JST | Read governance, policy, closed-family context, predecessor artifacts, and fixed validator; identified predecessor's duplicate-slippage formula. | Corrected formula and price-free arithmetic example recorded above. |
| 2 — 2026-09-16 JST | Retrieved JPX/Cabinet official calendar sources, including the final OSE list; handled recorded non-trading holiday exceptions and OSE trade-date rule. | Full 1,642-date contract generated without market input. |
| 3 — 2026-09-16 JST | Investigated continuation, already-impounded, and reversal explanations; recorded failed alternative article retrievals rather than inferring their results. | No direct C01 support located; limitation is explicit. |
| 4 — 2026-09-16 JST | Ran immutable `validate_c01_preparation.py`, inspected JSON coverage/source consistency, 28 calendar-only reopening candidates, local links, trailing whitespace, and worktree state. | Validator PASS (exit 0); the calendar-only audit passed.  The pre-existing dirty worktree was preserved and the four new files are the only changes made by this task. |
| 5 — 2026-09-16 JST | Re-read the official JPX holiday-rules page, TSE cash-days page, 2023-H2 final list notice, and the cited primary research bodies to repair the reviewer-facing evidence declaration. | The rules explicitly confirm the 2022-09-23 start, index-futures eligibility, the same-trade-date treatment of the preceding weekday night/holiday/following weekday day sessions, and the 2023-11-03 non-implementation.  No calendar value, hypothesis, price input, or execution authority changed. |
| 6 — 2026-09-16 JST | The web text reader could not render the official XLSX/CSV directly (safe-URL/content-type errors). Re-fetched both official public files in process memory and calculated their byte counts and SHA-256 hashes; no file was persisted. | CAL-2 remained 21,538 bytes / `cec37a743c96995cdb9cb52b685c9003634682a9b0e1a640a6b9b96881fe964a`; CAL-3 remained 11,567 bytes / `93daa202fe71cff97a7b3691d793a40d101ba4058c8dffe03550b6ee2ca29721`. Both exactly match the sealed contract snapshot, so no calendar repair was warranted. |
| 7 — 2026-09-16 JST | The first ad-hoc read-only Markdown-link checker emitted a Python regular-expression warning, so its exit-0 result was not accepted as evidence. Replaced the pattern with a simple link-target parser and reran it with a trailing-whitespace scan. | Clean pass: every local Markdown file target in the three Markdown artifacts exists and no trailing whitespace was found. This verification-only repair did not change the calendar, hypothesis, or protected state. |
