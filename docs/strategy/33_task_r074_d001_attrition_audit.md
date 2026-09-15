# TASK-R074-D001: R074-Q001 2021 S2 event-ledger attrition audit

`TASK-R074-D001` is a read-only, Development-only audit of the frozen
`R074-Q001` primary S2 event ledger. It does not change the R074-Q001
specification, 25-observation yearly S2 gate, implementation, or prior
artifacts.

The sole input is
`results/research/r074-q001-20260915-low-night-range-opening-breakout-01/s2_events_primary.json`.
The audit neither loads normalized bars nor accesses any PnL, return, win/loss,
PF, bootstrap, sensitivity, OOS, or Final Holdout artifact. It emits no price
level, range, order, fill, trade, or performance field.

Its exclusive waterfall first separates scheduled dates, night-window failure,
current-night R004 isolation, Development-boundary references, and reference
R004 isolation. After a 20-reference set is established, it branches into
A/D/high state; A then branches into opening-range validity, strict breakout by
11:00, entry/exit observability, and final long/short executability. Counts and
rates are reported for each 2021 month and 2021--2024 year, with 2022--2024 as
the descriptive comparison cohort. R004 effects retain both all-20-reference
membership (which may overlap) and the helper's direct first-reference failure
(which is exclusive).

The immutable audit result is
`results/research/task-r074-d001-20260915-development-s2-ledger-attrition-audit-04/`.
