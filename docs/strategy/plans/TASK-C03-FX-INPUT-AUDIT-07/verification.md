# Verification record

Task: `TASK-C03-FX-INPUT-AUDIT-07`
Verified: 2026-09-17 JST

## Permitted validation

The fixed structural validator was run before artifact creation (expected
failure: the five declared artifacts were absent), then rerun after the
artifacts below were created:

```powershell
.venv/Scripts/python.exe scripts/validate_c03_fx_input_audit.py --artifacts docs/strategy/plans/TASK-C03-FX-INPUT-AUDIT-07
```

The final invocation is recorded in the next section. Its scope is structural
only: task ID, constrained decision values, all six attributes, research
limits, false authorisation flags, and the six fixed synthetic cases. It does
not authenticate any supplier claim or admit an input.

## Final validator record

The frozen validator was rerun after this repair. It exited `0` and returned
`passed: true` with no issues:

```json
{
  "passed": true,
  "issues": [],
  "limitations": "Structure only; no FX value, external time series, market data, C03 execution, or economics is validated."
}
```

`stderr` was empty. This is the final structural-validation record for this
task; it is not evidence that OANDA documentation establishes historical
availability, that any USDJPY series was accessed, or that C03 is admissible.

## Document-to-artifact cross-check

| Check | Evidence | Result |
|---|---|---|
| One candidate only | `source_contract.md` identifies OANDA v20 historical candles only and expressly excludes a blend. | PASS |
| Source ledger is bounded | `input_admission.json` records 5 searches and 6 primary bodies, each at or below 8. | PASS |
| Required source attributes are individually classified | `input_admission.json` contains the six prescribed IDs with a status, basis, and evidence/absence reason. | PASS |
| Availability does not get inferred from bar time | `source_contract.md` separates candle bar-start semantics from as-of delivery, finality, and revision evidence. | PASS |
| Boundary behavior is fixed | `synthetic_timing_cases.json` contains exactly the six required case IDs and required handling values. | PASS |
| Ambiguity fails closed | The unknown label, later correction, and cross-provider cases are `FAIL_CLOSED`; late availability and an undocumented gap are `NO_SIGNAL`. | PASS |
| No C02/F11/C03 conflation | `source_contract.md` and `input_admission.json` preserve C02 `NOT_VERIFIED`, F11's closed/parked state, and C03 `NOT_EVALUATED`. | PASS |
| No market or external-series access | `input_admission.json` records false access/authorisation flags and zero market-data attempts. | PASS |

## Limits and unresolved conditions

The outcome is `NOT_VERIFIED`. The source materials did not establish all of
the following: a project-held, historically applicable licence and reproducible
archive; delivery latency; an as-of vintage/revision history; session and gap
semantics; and a documented OSE decision-time alignment rule.

These are research findings, not validator failures. The permitted remedy would
require a separately authorised, frozen audit scope; it is not attempted here.
No C03 family, execution, grant, manifest, market-data access, OOS, or Final
Holdout action follows from this verification.
