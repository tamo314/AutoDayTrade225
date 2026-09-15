# R094-Q001 result: full cash path efficiency to following-night continuation

Run ID: `task-r094-q001-cash-path-efficiency-night-continuation-20260915-01`  
Decision: **INCONCLUSIVE before PnL**

Immutable artifacts are in [task-r094-q001-cash-path-efficiency-night-continuation-20260915-01](../../results/research/task-r094-q001-cash-path-efficiency-night-continuation-20260915-01/). The frozen registration is [91_r094_q001_cash_path_efficiency_night_continuation.md](91_r094_q001_cash_path_efficiency_night_continuation.md).

## Pre-PnL record and validation

This was explicitly registered after R093 and after known R009/R046/R079/R088/R089/R093 results, as a repeatedly used-Development hypothesis with an INVESTIGATE ceiling. It did not adopt R093's continuation point estimate. The only physical market input was the Development normalized Parquet series; OOS, Walk Forward, Final Holdout, raw data, external cash data, and volume were not accessed.

The run saved its preregistration, input/config/code hashes, source/config/document snapshots, versioned TSE business-day axis, R004 isolation, full PnL-free event ledger, S2 gate, causality audit, and exit-code-bearing validation before any PnL path. `pytest` (13 passed), Ruff, mypy, and `py_compile` each returned 0.

The 1,099-day official TSE axis used all scheduled normal cash minutes, including exactly one 11:29-close to 12:30-close change. The current-excluded, non-backfilled prior-120-date references, old/new TSE/OSE rules, cash-to-following-night mapping, calendar/trade date separation, post-close submission, first/last normal night opens, R004 isolation, maximum position, and H/K future-night-selection audit all passed. There were no unexplained exclusions and no filled unresolved exits. The 17 reasoned cancellations were 15 first-normal-night entry unavailability and two R004 following-night quarantines.

## Fixed S2 result

q-ready nonzero cash paths were **957**. Completed H paths were **222** (positive/negative cash directions **133/89**; years **30/56/57/53/26** for 2021--2025H1; old/new OSE **189/33**) and satisfied every H minimum. The two rolling-x conditional bands also had adequate completed H/M support: `[0.50,0.75)` H/M **45/185** and `[0.75,1.00]` H/M **177/58**.

Completed K paths were only **243**, below the frozen required **250**. This single seven-path shortfall makes the AND gate false. Per the registration, processing stopped before trade construction, fill-price/return/PnL/PF computation, bootstrap, fixed sensitivity runs, execution-accounting economics, and the REJECT/INVESTIGATE primary analysis. No missing cases were backfilled and no threshold, direction, timing, or night interval was changed to rescue the gate.
