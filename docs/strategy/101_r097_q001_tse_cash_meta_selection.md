# TASK-R097-Q001: audited TSE cash-only strategy meta-selection

This is a one-time, post-R001--R096 Development meta-hypothesis. `prior_information_seen=true`; regardless of results the decision ceiling is **INVESTIGATE**. It never opens 2025H2 OOS or the 2026+ Final Holdout, changes no constituent strategy, and adds/removes no constituent after this registration.

Technical run `...-01` stopped before loading a daily-Net or trade ledger because it incorrectly required the legacy economic status within `COMPLETED.json` to equal `COMPLETE`. Its saved freeze shows only metadata and hashes. Run `...-02` corrected that serialization check, then stopped before performance reporting because five zero-count audit conditions were encoded as false rather than as passed `no_*` predicates. Run `...-03` changes only those boolean encodings; the registry, hashes, scores, thresholds, rules, and sensitivity plan are unchanged.

## Frozen constituent screen before evaluation PnL

R001--R096 are mechanically enumerated before reading their daily evaluation PnL. A principal strategy is eligible only if its saved, immutable artifact has all of: (1) trades wholly inside official TSE cash hours; (2) selection is possible by 08:45 JST before its same-date event, price, fill or availability; (3) one contract and no more than one position; (4) one-tick-per-side plus JPY30-per-side base costs; (5) an identical R004-isolated Development scheduled-date axis ending 2025-06-30; (6) PASS validation and an all-true execution/accounting audit; and (7) a reconciled primary daily-Net ledger plus complete-trade ledger. Economic result and previous REJECT/INVESTIGATE labels are not predicates.

The included `strategy_id`, `family_id`, artifact locations and SHA-256 hashes are written first to `constituent_freeze_before_evaluation_pnl.json`. Every other R001--R096 ID is written with the sole mechanical exclusion reason: it does not have the complete matching saved cash-only primary daily-Net/trade/audit bundle. The constituents are not rerun, repaired, or re-aggregated.

## Frozen meta rules

The common official scheduled TSE trade-date axis retains no-event dates as JPY0; no date is dropped for a same-date event, price, or fill result. The first 252 scheduled dates are calibration only and are excluded from evaluation. On each later date `d`, each strategy score is the mean base-cost daily Net over exactly the preceding 120 scheduled dates `[d-120,d)`. The current date is excluded. The unique maximum is chosen only if strictly positive; ties use frozen `strategy_id` ascending order; otherwise A is cash. A selection is made before any same-date signal, price, event, or fill is examined.

Q is cash when A is cash and otherwise routes to the same-score rank-two strategy. S is the one strategy with the greatest 252-date calibration mean (same ID tie-break), held fixed for every evaluation date. N is always cash. There is no fallback, switching, weighting, or parameter search.

Before PnL aggregation, stop INCONCLUSIVE unless: eligible strategies >=5; evaluation dates >=700; A non-cash selections >=150; completed A trades >=100; >=2 strategies receive >=20 A selections; 2022--2024 each have >=20 completed A trades; 2025H1 has >=10; and no future reference, same-day availability switch, axis mismatch, unexplained exclusion, or unresolved filled position exists.

If that gate passes, use a common-index, 20-trade-date non-wrapping moving-block bootstrap, 10,000 repetitions, seed `20260915`, tail truncation, and linear percentile. Primary AND: A Net>0, PF>1, A daily-Net CI lower>0, paired A-Q CI lower>0, and paired A-S CI lower>0. Any failure is REJECT.

After a primary pass, fixed sensitivities are 60/240-date lookbacks; base-path-fixed 2-tick-per-side and fee×2 costs (3 ticks diagnostic only); leave-one-family-out; and removing A's ten largest winning trades. The result remains INVESTIGATE if any fixed non-diagnostic Net is nonpositive, fewer than two of 2022--2024 and 2025H1 are positive, any family leave-out is nonpositive, or removing the top ten winners is nonpositive. Even all passes remain INVESTIGATE because Development has been repeatedly used.
