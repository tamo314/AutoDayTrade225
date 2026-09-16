# C03 USDJPY input source contract

Task: `TASK-C03-FX-INPUT-AUDIT-07`
Audit date: 2026-09-17 JST
Decision: `NOT_VERIFIED`

## Fixed scope and candidate

This is a document-only admission audit for the C03 external USDJPY input. It
does not retrieve, inspect, calculate, or display an external time series or
any market-derived value.

The sole candidate examined is **OANDA Corporation, v20 REST API, authenticated
historical candlestick distribution for the provider's USDJPY instrument**. No
second provider, instrument, screen, download, API response, or source blend
was used. `USD_JPY` is the candidate pair identifier: SRC-06 defines an
instrument name as base currency and quote currency separated by `_`, and
SRC-02/SRC-06 define the documented bid, ask, and midpoint components. No
component is selected or admitted for C03 by this audit.

The candidate is retained as a named documentation target only. It is not an
approved input, a C03 family, a registered experiment, or an authority to
obtain data.

## Primary-source ledger

All entries below are provider-owned documentation or provider-owned contract
material. `Accessed` is 2026-09-17 JST. A missing version/date is recorded as
missing rather than inferred.

| ID | Provider document and URL | Version or date shown | Supports | Does not support / remains unconfirmed |
|---|---|---|---|---|
| SRC-01 | [OANDA v20 REST API — Introduction](https://developer.oanda.com/rest-live-v20/introduction/) | Revision date not stated; footer shows 2014 | An account and personal token are required; the page says historical pricing information reaches back to 2005. | An entitlement held by this project; a reproducible target-period archive; as-of availability, latency, corrections, or session semantics. |
| SRC-02 | [OANDA v20 — Instrument definitions](https://developer.oanda.com/rest-live-v20/instrument-df/) | Revision date not stated; footer shows 2019 | M1 is a documented candle granularity; a candle `time` is its start; bid, ask, and midpoint components are separately described; `complete` means its ending time is not in the future. | The USDJPY quote direction; which component C03 may use; delivery latency; archival immutability; revisions; or holiday/missing-row meaning. |
| SRC-03 | [OANDA v20 — Pricing definitions](https://developer.oanda.com/rest-live-v20/pricing-df/) | Revision date not stated; footer shows 2019 | A stream Price and heartbeat have provider-created times; a candle specification includes instrument, granularity, and component. | A bounded latency between creation and client availability; completeness of a client capture; a correction/as-of record; or an OSE comparison rule. |
| SRC-04 | [OANDA — REST v20 API troubleshooting guide](https://help.oanda.com/au/en/faqs/rest-v20-api-troubleshooting-guide.htm) | Revision date not stated | Authenticated API access is required; historical candles can differ from live account-specific pricing; the candle endpoint is described as probing after a bar closes. | The historical data's availability timestamp, a maximum delay, retained vintages, a revision log, or a calendar/missingness contract. |
| SRC-05 | [OANDA Corporation API License Agreement](https://legal.oanda.com/oc/api_license_agreement_oc/en) | August 2026 | Licensed Materials include the rate feed, rates, documentation, and updates; Internal Use includes research/analysis and excludes redistribution to third parties. | The agreement or entitlement applicable to this project during 2021-01-01 through 2025-06-30; permission to store/reproduce a target-period data set; or an archival/revision guarantee. |
| SRC-06 | [OANDA v20 — Primitive definitions](https://developer.oanda.com/rest-live-v20/primitives-df/) | Revision date not stated; footer shows 2019 | An instrument name is base currency and quote currency delimited by `_`; documented components are midpoint, bid, and ask; `DateTime` is RFC 3339 or Unix time from the UTC epoch. | The source's delivery timestamp, timestamp-to-availability relationship, or a session/holiday rule. |

The documents support that OANDA has an authenticated historical-candle
interface and that its candle label is a start time. They do **not** establish
the project-specific, decision-time provenance needed for C03. In particular,
SRC-04 explicitly distinguishes historical candles from account-specific live
pricing, so retrospective historical retrieval cannot be treated as a record
of what was available at a past N225 decision time.

## Attribute admission ledger

| Attribute | Status | Evidence / reason |
|---|---|---|
| `instrument_definition_and_quote_convention` | `VERIFIED` | SRC-06 defines the underscore name as base/quote currency and the available components; SRC-02 defines their candle representation. This documents the candidate source convention, while this audit deliberately selects no C03 component. |
| `historical_access_and_license` | `UNVERIFIED` | SRC-01 states historical access reaching back to 2005 and requires an account/token. SRC-05 states current internal-use restrictions. Neither proves a target-period archive, this project's entitlement, reproduction/storage terms, or the applicable historical contract. |
| `timestamp_timezone_and_bar_label` | `VERIFIED` | SRC-02 defines candle `time` as the start time and lists M1; SRC-06 defines the documented time representations and UTC epoch. This verifies API timestamp representation and bar-start label only, not availability time. |
| `availability_latency_finality_revision` | `UNVERIFIED` | SRC-02's `complete` condition and SRC-04's after-close probing do not give a decision-time delivery bound, provisional/final lifecycle, correction history, or reproducible as-of vintage. SRC-04's historical/live distinction prevents inferring one. |
| `session_calendar_and_missingness` | `UNVERIFIED` | None of SRC-01 through SRC-06 defines session boundaries, holidays, halts, absent-row semantics, or continuity rules for the candidate historical distribution. |
| `n225_alignment_and_causality` | `UNVERIFIED` | The OANDA materials do not define an OSE decision-time comparison or establish that a candidate observation was available before it. Without an available-at timestamp and a documented comparison rule, causality must fail closed. |

`CONTRADICTED` is not used: the public documents leave required facts absent;
they do not prove their inverse. The overall decision is therefore
`NOT_VERIFIED`, not an inferred approval or a claim of nonexistence.

## Bounded public-research log (cumulative)

This task has used **5 / 8** search queries and **6 / 8** primary-source bodies.
No further query or source body is needed for this frozen task.

| Query ID | Exact query | Outcome |
|---|---|---|
| Q01 | `site:developer.oanda.com/rest-live-v20/instrument-ep/ candles endpoint complete time granularity` | Located provider instrument/candle definitions (SRC-02). |
| Q02 | `site:developer.oanda.com/rest-live-v20/pricing-ep pricing stream time OANDA` | Located provider pricing definitions and the linked primitive time specification (SRC-03, SRC-06). |
| Q03 | `site:help.oanda.com API historical data candles maximum OANDA v20` | Located the provider troubleshooting guide on authenticated access, historical/live distinction, and after-close candle retrieval (SRC-04). |
| Q04 | `site:oanda.com legal API market data redistribution OANDA` | Located the provider API licence agreement (SRC-05). |
| Q05 | `site:developer.oanda.com/rest-live-v20 "USD_JPY"` | Located provider documentation showing the candidate identifier in API documentation; no response or data endpoint was opened. It did not cure the definition gap. |

Search-result snippets, documentation examples, screens, and endpoint response
examples were not used as data. No CSV, API response, download, historical
record, or external time-series body was requested.

## Deliberate separations

- C02 remains its own volume-input audit: its preserved decision is
  `NOT_VERIFIED`. Nothing in this USDJPY documentation resolves C02 volume
  semantics or changes R010/R052's `INPUT_OR_QUALITY_BLOCK` classification.
- F11 remains `PARKED` with zero remaining variants and no automatic reopening,
  as preserved by the C02 artifact and the closed-family registry. This task
  neither examines nor reopens it.
- C03 is not a family and has not been economically evaluated. `NOT_VERIFIED`
  means only that its proposed external input is not admitted on this evidence;
  it is not a performance result or a reopening route for any closed family.

## Non-authorisation boundary

This source contract authorises none of the following: external time-series
access, market-data access, C03 family creation, a manifest, a grant, a market
run, OOS, Final Holdout, engine changes, or changes to existing results or the
registry. The task's market-data attempts remain zero.
