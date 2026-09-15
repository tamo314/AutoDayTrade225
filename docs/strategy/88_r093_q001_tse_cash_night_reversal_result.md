# R093-Q001 result: TSE cash extreme direction to following OSE night reversal

Run ID: `task-r093-q001-tse-cash-night-reversal-20260915-01`  
Decision: **INCONCLUSIVE — stopped before PnL**

The immutable artifacts are in [task-r093-q001-tse-cash-night-reversal-20260915-01](../../results/research/task-r093-q001-tse-cash-night-reversal-20260915-01/). The frozen specification is [87_r093_q001_tse_cash_night_reversal.md](87_r093_q001_tse_cash_night_reversal.md).

The pre-execution validation passed: the R093 synthetic tests plus execution tests (12 tests), Ruff, mypy and `py_compile` all returned exit code 0. The run used only Development normalized Parquet, the frozen TSE business-day evidence and the versioned local exchange calendar. It did not access 2025H2 OOS or the Final Holdout.

PnL-free S2 availability/count gates all passed. The scheduled TSE axis contained 1,099 dates. There were 957 q-ready nonzero-return cash days and 253 E orders (26.4368%). Of the orders, 246 were complete cash-to-night paths; positive/negative cash returns were 126/120. By cash year: 2021=27, 2022=62, 2023=69, 2024=65, and 2025H1=23. Old/new OSE regimes were 215/31. There were no unexplained exclusions and no filled unresolved exits.

However, the PnL-free causality audit failed the predicate `references_are_exactly_current_excluded_prior_120_when_q_ready`. Its implementation applied the allowed-length test `{0,120}` to every scheduled row, including normal warm-up rows that correctly contain 1 through 119 prior scheduled dates. Thus the audit result was false even though the S2 count gate passed. This is an implementation/audit defect found before any trade, fill-price, return, PnL, PF, bootstrap, sensitivity, or execution-accounting artifact was generated.

Under the frozen task rule that any defect or inconsistency is **INCONCLUSIVE before PnL**, the run stopped. No repair rerun, threshold/direction/time modification, Walk Forward, OOS, or Final Holdout work was performed. The immutable record retains the exact failed predicate and complete pre-PnL ledger; it must not be used as economic evidence.
