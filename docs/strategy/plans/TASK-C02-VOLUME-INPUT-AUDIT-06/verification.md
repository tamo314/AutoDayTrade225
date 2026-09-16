# TASK-C02-VOLUME-INPUT-AUDIT-06 — verification

## Permitted validation

The sole fixed validator is run against this artifact directory only:

```powershell
.venv/Scripts/python.exe scripts/validate_c02_volume_input_audit.py --artifacts docs/strategy/plans/TASK-C02-VOLUME-INPUT-AUDIT-06
```

The command and its exit code/output are recorded below after execution. It checks artifact presence and JSON structure, the 8/8 research limits, all five attribute IDs and valid status labels, the required non-authorisation flags, and the six fixed synthetic cases.

### Execution record

Executed 2026-09-17 JST from the repository root. Exit code: **0**.

```json
{
  "passed": true,
  "issues": [],
  "limitations": "Structure only; no volume value, market data, C02 execution, F11 reopening, or economics is validated."
}
```

## Source-to-attribute cross-check

|Question|Permitted-source result|Artifact handling|
|---|---|---|
|Does the provider offer a 1-minute mini series?|Yes: SRC-06 says the mini product has a 1-minute interval; SRC-06 also describes a central-contract series.|This does not identify the `volume` field. `per_minute_quantity=UNVERIFIED`.|
|Are exchange product and planned session boundaries known?|Partly: SRC-02/03/04 identify product hours and the institutional trade-day convention.|No provider timestamp edge or calendar-date mapping is documented. `timestamp_timezone` and `trade_date_and_session_boundary` remain `UNVERIFIED`.|
|Are finality, corrections, missing rows, and zeros documented for the provider file?|No retrieved source documents them.|`bar_finality_and_corrections` and `missing_zero_semantics` remain `UNVERIFIED`; the corresponding synthetic cases are fail-closed or require a policy.|

## Boundary-case inspection

`synthetic_volume_cases.json` contains exactly the required six cases. It deliberately uses only artificial labels and raw-volume placeholders. It contains no price, VWAP, return, signal, order, fill, PnL, count derived from market data, or computation. The expected handling prevents silent conversion of cumulative, provisional, revised, cross-session, missing, or zero observations into a causal volume input.

## Limits and interpretation

The validation result is structural only. A pass does **not** verify the truth of any volume field, allow real-data access, reopen F11, execute C02, change a grant or manifest, establish a causal VWAP, or establish economic profitability. The public materials establish product and schedule context but not the target file's input semantics; the correct research decision therefore remains `NOT_VERIFIED`.
