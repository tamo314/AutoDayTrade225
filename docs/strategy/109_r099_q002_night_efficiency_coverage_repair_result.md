# TASK-R099-Q002 result — REJECT

Final immutable run: `results/research/task-r099-q002-night-efficiency-coverage-repair-20260916-02/`.

Before Q002 parsed any PnL field, it reproduced Q001's PnL-free stop: scheduled axis=1,131, current-night unavailable=265, the other 866 dates had fewer than 100 complete nights in Q001's strict prior-120 window, and q-ready=0. Q001 had no daily-Net, PF, bootstrap, Delta, or sensitivity output. R078-A/R079-A hashes matched the Q001/R097 freeze; all 230 E dates, entries/exits, one-contract quantities, and opposite sides matched.

The registered coverage-only repair used only the latest 100 complete nights within the strict prior 180 scheduled TSE dates. The PnL-before-evaluation audit passed: q-ready=766 (HE/LE/ME=252/256/258), A completed=116 (HE/R079=55; LE/R078=61), long/short=51/65, 2022/2023/2024/2025H1=26/40/28/15, old/new=95/21, and the six HE/LE by range-band common-event cells were 33/10/12 and 18/25/18. Every requested future-reference, current-date availability, R004, explained-exclusion, and unresolved-position check passed. The complete-night distribution and unavailable reasons by year and regime are retained in `coverage_audit_before_evaluation_pnl.json`.

On the common 1,131-date axis, A Net was **−95,460 JPY** and PF **0.886**. The 20-trade-date non-wrapping MBB (10,000, seed 20260916) lower bounds in JPY/trade-date were A daily Net **−494.19**, A−C **−756.87**, A−F **−302.41**, A−I **−780.75**, and Delta **−4,597.48** (Delta point estimate +107.28). Thus every primary-AND requirement failed. The decision is **REJECT**; per the frozen rule no threshold/lookback/cost sensitivity, Walk Forward, 2025H2 OOS, or Final Holdout was accessed.

The prior `...-01` directory is retained as an execution-failure record: a JSON key serialization error occurred while writing the PnL-free coverage audit, before daily-Net/PF/bootstrap/Delta access. Its `EXECUTION_FAILED.json` records the exact exception. The final `...-02` run made only that serialization repair and did not alter the registered state, execution, costs, or inference.
