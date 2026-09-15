# TASK-R099-Q001: official-night direction-efficiency fixed switch

Status: **FROZEN_BEFORE_EVALUATION_PNL**  
Registered: 2026-09-16 JST  
Family: `cash_first_hour_extreme_night_efficiency_switch`; study: `R099-Q001`; version: `v1`

## Hypothesis and scope

This is a one-time, Development-only, post-hoc hypothesis after R078, R079, R097 and R098. It is not a rescue of a performance-ranked family. `prior_information_seen=true`, repeated Development use, and a hard decision ceiling of **INVESTIGATE** are recorded. It may not access 2025H2 OOS, Final Holdout, Walk Forward, new states, boundaries, strategies, scores, weights, fallbacks, or altered entry/exit rules.

For a trade date, the corresponding complete official NIGHT through 05:29 is known by 08:45. Let O be its first eligible normal-bar open, C its final eligible normal-bar close, and H/L its normal-bar high/low. When H>L, `e=abs(C-O)/(H-L)` and `v=H-L`. From exactly the preceding 120 scheduled TSE business dates, excluding the current date and without backfill, retain complete nights only. With at least 100 valid observations and nearest-rank q33(e)<q67(e), classify LE `e<=q33`, ME `q33<e<q67`, HE `e>=q67`; otherwise state unavailable. The same strict-prior sample produces q33/q67 v bands solely for stratified mechanism evaluation.

R078-A and R079-A are immutable inputs: the R097 frozen hashes, 1,131-day axis, event ledger, entries, exits, and base one-tick-plus-JPY30 ledger must be reused unchanged. Before PnL parsing they must hash-match R097 and prove every E event has identical event/date, entry, exit, one contract, and opposite side. Any failure is INCONCLUSIVE without PnL.

## Fixed routes, gates, and inference

A selects R079-A in HE, R078-A in LE, and cash in ME/unavailable. C uses R079-A in HE/LE; F uses R078-A in HE/LE; I reverses A's HE/LE mapping; N is always cash. Routing is fixed before each date's constituent event; a missing event is JPY0 and later price/fill/exit availability cannot switch it.

Base cost is one contract, one tick each side plus JPY30 each side. PnL-free gate: q-ready>=800; HE/LE>=250 each; complete A>=100; HE/R079 and LE/R078>=35 each; A long/short>=35 each; 2021 initialization partial>=10; 2022--2024>=20 each; 2025H1>=10; old/new schedule>=85/10; every HE/LE by v-band common-event cell>=8; no future reference, same-day availability filtering, R004 contamination, unexplained exclusion, or unresolved filled position. Failure is INCONCLUSIVE before PnL.

Use one common 20-trade-date non-wrapping, tail-truncated moving-block bootstrap of the full scheduled axis, 10,000 repetitions, seed 20260916, linear percentile. Evaluate A daily Net and paired A-C/F/I. For each resample recompute `G=Net(R079)-Net(R078)` and equally weight the three v bands: `Delta=(mean(G|HE,v)-mean(G|LE,v))/3` summed over bands.

Primary AND: A Net>0, PF>1, lower A daily-Net CI>0, lower paired A-C/F/I CIs>0, and lower Delta CI>0. Any failure is REJECT. Regardless of result, save fixed q25/q75 and q40/q60 thresholds, 60/240 prior scheduled-date windows, and fixed-route 2/3 tick and double-fee diagnostics; they cannot rescue primary failure. After a pass, INVESTIGATE still requires every threshold/window, 2 tick and double fee to have A Net>0 and Delta>0, both A legs positive, at least two 2022--2024 years and 2025H1 positive, both regimes positive, and top-ten winners removed Net>0. Even then the ceiling is INVESTIGATE.
