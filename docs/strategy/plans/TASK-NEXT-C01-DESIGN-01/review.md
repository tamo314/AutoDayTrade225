# C01 design review — TASK-NEXT-C01-DESIGN-01

## Decision

**Design decision: HOLD.** This is a document-level decision under
[124](../../124_next_research_orchestrator_handoff.md), not one of the research
`REJECT`/`INVESTIGATE`/`CANDIDATE` enums and not an execution authorization.
The frozen documentation task itself is complete when the stated checks below
pass.

### Reasons

1. New direct institutional evidence establishes a material classification
   distinction: from 2022-09-23, eligible index futures could trade on OSE
   holiday-trading dates while the TSE cash market remains closed on its listed
   holidays. It supports a post-boundary calendar selector, not a return or
   PnL prediction.
2. The closest historical family is closed. R087-Q001 was PnL-free
   `INCONCLUSIVE` because its frozen information gates failed; R101-Q001 was a
   cost-after `REJECT` with Net −52,600 yen and negative fixed sensitivities.
   A calendar label does not repair either record or supply a F05/F06 exception.
3. No frozen official 2021--2025H1 cash/OSE holiday mapping is available in
   this task, so the exact treatment/control calendar labels, trade-date map,
   and calendar hash are unverified.
4. Minimum economically meaningful effect, required precision, group minima,
   capital, allowable drawdown, and operating minimum are unspecified. They
   are deliberately null rather than copied from an old gate or invented.
5. No non-PnL availability audit or synthetic calibration was run. Therefore
   there is no basis to assert S0/S1/S2 PASS, a suitable cohort size, or an
   executable complete specification.

The HOLD does not say the true C01 expectation is negative. It says the
available design/evidence is insufficient to seek an execution exception.

## Existing-research delta and new evidence

The only new evidence is the official **institutional** distinction documented
in [evidence.md](evidence.md): JPX's 2021 announcement and current holiday
trading rules identify the 2022-09-23 start and list index futures as eligible;
JPX cash-market materials describe cash holiday closures. C01 adds no night
price, gap, volume, external series, cross-session holding, changed R087
rolling feature, or changed R101 two-stage confirmation. It instead proposes a
single, untested post-boundary calendar label paired with the same first-15
minute direction rule and ordinary-opening control.

There is **no** new evidence that the calendar label predicts continuation,
creates enough usable observations, improves on R101, or is economically
profitable.

## Remaining conditions

The maximum one permitted follow-on task, if separately reviewed rather than
automatically dispatched, is:

> Obtain and version a first-party 2021-01-01--2025-06-30 cash/OSE holiday
> calendar mapping; document the precise treatment/control labels and official
> effective dates; run only synthetic mapping/causality fixtures. Do not read
> market data, calculate counts/PnL, change the C01 route, create a manifest,
> or allocate a grant.

It can proceed only if a closure-family review accepts that the new calendar
evidence is sufficient to prepare the S1 audit. It must preserve the null
economic requirements or obtain their owner-specified values through the
appropriate governance process. A later non-PnL S2 would additionally need a
complete frozen specification, S1 success, a registered manifest,
ReviewReceipt, matching grant, and actual remaining budget. No next task has
been dispatched here.

## Access and change record

### Access actually performed

- Repository documentation, calendar/interface specifications, frozen
  orchestration configuration, execution setting, and closed-family registry
  were read. Relevant historic records were R087/R101 and the F05/F06 index.
- Four JPX primary pages were read; one JPX-only search query was used to find
  cash-market holiday material. Cumulative external use is 1/8 queries and
  4/8 primary-source bodies, recorded in [evidence.md](evidence.md).
- No market data or price-derived material was accessed: raw/normalised bars,
  volume, features, trade records, price cache, and R065 payload were not
  opened. OOS 2025H2 and Final Holdout 2026+ were not accessed.
- No backtest, research runner, CLI, old orchestrator, Python strategy code,
  data loader, market query, external time-series request, purchase, or
  external enquiry was run.

### Changes actually made

Only these required Markdown artifacts were added:

- [evidence.md](evidence.md)
- [design.md](design.md)
- [review.md](review.md)

No protected configuration, registry, existing run, engine, strategy, grant,
manifest, or orchestration state was changed. In particular,
`config/research_execution.json` remains a stopped configuration with
`market_execution_enabled=false` and zero grants; the registry remains
unchanged and continues to close F05/F06 with zero remaining budget.

## Documentation checks

The completion checks to be run after writing these files are:

| Check | Required result |
|---|---|
| Required artifacts | All three files exist, are non-empty, and are the only intended worktree changes. |
| Relative local links | All local relative Markdown targets resolve; public JPX links are recorded with source date (when shown), access date, supported claim, and unsupported claim. |
| Scope completeness | Evidence includes prior/near-family history, old-stop/new-evidence mapping, C02--C05 handoff, and cumulative research-search limits; design contains one C01 route, information sets, costs, cohorts, stresses, S1/S2 and calibration contracts. |
| Integrity | `git diff --check` passes; diff confirms the execution configuration and closed registry are unchanged. |
| Tests | No pytest, Ruff, or mypy run: only Markdown changed, and the frozen task explicitly says these are unnecessary. |

The actual command results are appended below after verification; a failed
check would leave this task incomplete rather than changing scope.

## Verification result

Completed after all three artifacts were written:

| Check | Actual result |
|---|---|
| Required artifacts | 3/3 exist and are non-empty: `evidence.md`, `design.md`, and `review.md`. |
| Worktree scope | `git status --porcelain --untracked-files=all` reports exactly those three new files; no unrelated change was present. |
| Local links / whitespace | A read-only Markdown-link check resolved every local relative target in the three files; no trailing whitespace was found. |
| Diff integrity | `git diff --check` exit code 0. |
| Protected state | `git diff -- config/research_execution.json docs/strategy/registry/20260916_review.json` had 0 lines and `git status` for both paths was empty. |
| Tests / static checks | Not run, as permitted for this Markdown-only frozen design task. |

The check is documentation integrity only. It does not validate a calendar
mapping, any S1/S2 contract, or market economics.
