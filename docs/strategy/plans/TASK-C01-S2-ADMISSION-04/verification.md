# C01 S2 admission design verification

Task: `TASK-C01-S2-ADMISSION-04`
Scope: **structure and synthetic metadata only; no market input, PnL, or execution.**

## Fixed-input verification

The two read-only input digests recorded in `admission_matrix.json` were
computed from the frozen source bytes and agree with the S1 audit's inherited
calendar digest:

| Input | SHA-256 | Result |
|---|---|---|
| C01 preparation calendar contract | `6822242ec5f2f7b17bdf7248a13b7099fccb6fa15ded5bd834b90b241d642800` | Matched |
| C01 S1 label audit | `34ca215a726b382714da940b4cc33fbac682d293f411d6803feffd22c7cd94df` | Matched |

The execution setting was read only: `market_execution_enabled=false` and the
grant array is empty.  The C01-adjacent F05/F06 registry status is closed with
no automatic reopening.  These observations establish the current
`NOT_GRANTED` record; they do not create an exception, budget, receipt,
manifest, or grant.

## Synthetic admission coverage

| Case | Expected result | Fixed fail-closed property |
|---|---|---|
| `no_grant` | `REJECT` | Missing exact grant. |
| `no_family_exception` | `REJECT` | Closed F05/F06 route has no exception review. |
| `calendar_hash_mismatch` | `REJECT` | Frozen calendar/S1 input identity changed. |
| `out_of_development_range` | `REJECT` | Request is outside the protected Development trade-date range. |
| `prohibited_output_field` | `REJECT` | Request includes a value/outcome field. |
| `hypothetical_complete_metadata_only` | `ACCEPT_SYNTHETIC_ONLY` | Complete simulated metadata shape only; `authorises_real_access=false`. |

The allow-list contains only dates, inherited label, four bar-presence flags,
entry/exit availability flags, missingness, and calendar-axis count.  Its
intersection with the prohibited list is empty.  This is a schema check, not
evidence about the existence, quality, number, price, direction, or outcome of
any real bar.

## Fixed-validator result

Required command:

```powershell
.venv/Scripts/python.exe scripts/validate_c01_s2_admission.py --artifacts docs/strategy/plans/TASK-C01-S2-ADMISSION-04
```

Result: **PASS, exit code 0**.  The validator checked the declared artifacts,
both frozen input hashes, the exact current `NOT_GRANTED` authorisation object,
all six required admission-gate identifiers, the allowed/prohibited field
boundary, and the exact six synthetic decision cases.  It was rerun after the
scoped whitespace repair described below and again passed with exit code 0.

## Limits and progress record

The initial read path named `129_c01_representative_route.md`, which is not
present; repository discovery identified the intended read-only C01 S1 task as
`129_c01_s1_calendar_causality.md`.  It was read instead; no source artifact
was changed.  The fixed S2 validator needed no contract repair.  A subsequent
cross-artifact whitespace audit found only trailing Markdown spaces on the task
line of this file, `s2_admission_spec.md`, and `review.md`; those spaces were
removed without changing scope, data access, hashes, decisions, or fields.

This task made no public-source request, market-data read, price/PnL attempt,
runner dispatch, grant/budget operation, protected-file edit, or orchestrator
call.  Cumulative records remain one C01 hypothesis, zero real-data/PnL
attempts, 21 public query strings, and 32 public source-body review events.
Those source-ledger figures are inherited records, not a new search budget or
evidence of economic validity.  C01 economics, causality, and execution remain
`NOT_EVALUATED`.
