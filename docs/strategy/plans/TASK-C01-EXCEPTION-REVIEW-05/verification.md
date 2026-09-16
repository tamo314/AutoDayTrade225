# C01 exception-review verification

Task: `TASK-C01-EXCEPTION-REVIEW-05`
Scope: **document, metadata, and fixed synthetic decisions only.**

## Read-only input identity

The hashes below were calculated from the specified source bytes before this
task wrote its declared artifacts.  They identify the comparison inputs; they
do not imply that any price, market-derived count, or execution payload was
read.

| Input | SHA-256 |
|---|---|
| Allowed read-only execution configuration (`config/research_execution.json`) | `15c1702e8e0e5072f0acdbbd1d7fb46b7e8fee29be1a75588712ac10db54a3cb` |
| Closed-family registry | `460c48e21343ffe34c5cd72c7fafafb2fbba38d84e9be130d07b4321b82d92b3` |
| R087/Q002 preserved result | `6c704c4b9ed46b5b778fbeac0eb04647be98ba6d77ed19bfeae9e1c34c5e506a` |
| R101/Q001 preserved result | `ce36226231b53de7ecbee4941c7a56bbf62fe8c6dfb778ec905ce0ab176aaf0f` |
| C01 preparation calendar contract | `6822242ec5f2f7b17bdf7248a13b7099fccb6fa15ded5bd834b90b241d642800` |
| C01 S1 label audit | `34ca215a726b382714da940b4cc33fbac682d293f411d6803feffd22c7cd94df` |
| C01 S2 admission matrix | `46a9ccc83be917fee9947720458767561953d7d288f8db332d06e3164a628da3` |

The permitted configuration check was read-only: it reports
`market_execution_enabled=false` and an empty configured grants array. It did
not modify configuration, operate a grant, or access a separate grant record.
The inherited S2 matrix and registry separately record F05/F06 closed, no
automatic reopening, real market input `NOT_GRANTED`, and real-data/PnL
attempts `0`.

## Structural audit

The declared comparison matrix is required to preserve the registry and S2
matrix hashes, the exact `NO_EXCEPTION_RECOMMENDED` decision, the two required
prior records, the five fixed blockers, and six finite reconsideration
requirements.  The synthetic artifact is required to contain exactly these
non-authorising decisions:

| Synthetic case | Expected decision | Real access |
|---|---|---|
| `calendar_novelty_only` | `DENY` | false |
| `small_route_variation` | `DENY` | false |
| `future_unverified_economic_claim` | `REQUIRE_SEPARATE_REGISTERED_REVIEW` | false |

The manual document audit also checks that the comparison distinguishes:

- the C01 calendar label from a direct economic claim;
- R087/Q002's sealed economic `REJECT` from the R087/Q001 lineage's preserved
  information insufficiency; and
- R101/Q001's sealed economic `REJECT` from a conclusion about C01.

No new public-source search was made for this task.  The inherited source
ledger remains 21 query strings and 32 source-body review events; it is a
historical record rather than a remaining research or execution budget.

## Fixed-validator result

Required command:

```powershell
.venv/Scripts/python.exe scripts/validate_c01_exception_review.py --artifacts docs/strategy/plans/TASK-C01-EXCEPTION-REVIEW-05
```

Result: **PASS, exit code 0.**

```json
{
  "passed": true,
  "issues": [],
  "limitations": "Exception-review structure only; no exception, grant, market access, PnL, economics, or feasibility is approved."
}
```

The post-validator final read-only audit also passed.  It confirmed that the
directory contains exactly the five declared non-empty artifacts; both JSON
artifacts parse; the two parent hashes match; the decision, five blockers, six
reconsideration requirements, and three synthetic decisions match the fixed
contract; F05/F06 remain closed; grant count and real-data/PnL attempts remain
zero; and no trailing whitespace remains.  The audit itself read no market
input.

Repair record: the first final-audit attempt detected three Markdown
hard-wrap trailing-space sequences in this task's three Markdown files.  They
were removed as a formatting-only scoped repair; no decision, input hash,
route, gate, source, access state, or JSON value changed.  The validator and
final audit then passed as recorded above.  A structural PASS will not be
treated as an exception, grant, real-data access, economic result, causal
result, or feasibility result.

Access-record repair: review feedback found that the earlier wording blurred
the permitted read-only `config/research_execution.json` check with forbidden
execution-control and grant access. `comparability_matrix.json`, this file,
and `review.md` now distinguish the read-only configuration check from the
prohibited configuration change, engine access, market access, and direct
grant/RunManifest/ReviewReceipt-artifact access. No source outcome, route,
gate, input hash, synthetic decision, budget, or authority changed. After
this wording-only repair, the fixed validator and the final structural audit
were re-run and passed with exit code 0.

## Access and limits

This task made the specifically allowed read-only check of
`config/research_execution.json`; it neither changed that configuration nor
treated its empty configured grants array as access to a grant artifact. It
otherwise read only the listed governance, registry, preserved-result, C01
preparation/S1/S2, and validator material. It did not access market data,
prices, volume, features, transaction records, PnL, OOS, Final Holdout, engine,
or any grant, RunManifest, or ReviewReceipt artifact. It made no external
request. The only intended writes are the five declared files in this task
directory.
