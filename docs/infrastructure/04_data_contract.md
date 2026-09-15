# 04. Canonical Data Contract

## 1. Minute bar

The canonical bar schema is machine-readable in `schemas/bar_schema.yaml`.

Core columns:

| Column | Type | Required | Meaning |
|---|---|---:|---|
| `ts_jst` | datetime tz=Asia/Tokyo | yes | Actual calendar timestamp of the minute bar |
| `trade_date` | date | yes | OSE trading date |
| `calendar_date` | date | yes | Calendar date of `ts_jst` |
| `session` | enum | yes | `day` / `night` |
| `schedule_version` | string | yes | Historical session regime id |
| `instrument` | string | yes | `N225M` |
| `series_type` | enum | yes | `center_continuous` / `next_continuous` / future `contract` |
| `contract_month` | string/null | yes | null for 225Labo continuous data |
| `open` | int | yes | JPY index points |
| `high` | int | yes | JPY index points |
| `low` | int | yes | JPY index points |
| `close` | int | yes | JPY index points |
| `volume` | int/null | no | Source volume if available |
| `is_session_open` | bool | yes | bar corresponds to session opening minute/auction when identifiable |
| `is_session_close` | bool | yes | bar corresponds to session closing minute/auction when identifiable |
| `is_missing_prev_expected` | bool | yes | expected previous minute missing |
| `roll_risk` | bool | yes | legacy conservative calendar/risk marker; false is not evidence of no observed roll |
| `is_eligible` | bool | yes | legacy QC flag; not sufficient for decision-time entry eligibility without an as-of audit |
| `quality_flags` | list[str] | yes | zero or more quality codes |
| `source` | string | yes | `225labo` |
| `source_file` | string | yes | lineage |
| `source_row_number` | int | yes | lineage |

## 2. Price rules

For N225M:
- integer JPY prices
- expected tick grid: multiple of 5 JPY for ordinary exchange trading records
- high >= open and close
- low <= open and close
- high >= low

Tick violations are quality errors/warnings depending on source context; do not auto-round.

## 3. Volume

Volume is nonnegative integer when present. Zero is legal and must not be treated as missing.

## 4. Trade ledger

See `schemas/trade_schema.yaml`.

Required economic fields:
- side
- qty
- entry/exit timestamps
- signal timestamps
- raw/reference prices
- fill prices
- gross PnL
- fees
- slippage cost
- net PnL
- MAE/MFE
- holding minutes
- entry/exit reason
- strategy id/version
- parameter hash

## 5. PnL

For quantity `q` and multiplier 100:

Long:
`gross_pnl = (exit_fill - entry_fill) * 100 * q`

Short:
`gross_pnl = (entry_fill - exit_fill) * 100 * q`

`net_pnl = gross_pnl - fees - other_costs`

If slippage is already embedded in fill prices, do not subtract it a second time. `slippage_cost` is an attribution field computed relative to reference/no-slippage fills.

## 6. Data schema versioning

Use semantic-ish integer versions such as:
- `bar_schema_version: 1`
- `trade_schema_version: 1`

Breaking column/semantic changes increment the version and require migration notes.

## 7. Versioned research sidecar — RG-20260915-01

既存bar/trade schemaを無断で破壊変更しない。以下を`research_contract_version: 2`の別表・manifestとして追加する設計とし、実装時に移行・互換性をテストする。旧研究のdata hashを追加列の存在だけで再生成しない。

|フィールド|意味・nullの扱い|
|---|---|
|`bar_interval_start`, `bar_interval_end`|根拠のある開始／終了時刻。不明はnullと利用制限|
|`price_available_at`, `volume_available_at`|取引判断に使える最早時刻。観測値か仮定かを別列に記す|
|`quality_available_at`, `quality_detected_at`|当時の判定可能時刻と監査発見時刻を分ける|
|`available_at_basis`, `evidence_id`|provider_document / observed_feed / modeled_bar_close / unknown等の根拠|
|`roll_observation_status`|known / unknown。legacy boolのfalseから推定しない|
|`observed_contract_change`, `contract_id`, `adjustment_method`|供給・検証された値。未確認はnull|
|`scheduled_axis_id`, `universe_version`|固定予定母集団・集合生成規則|
|`decision_at`, `max_input_available_at`|全依存入力が判断時点以下であることの証跡|
|`exec_eligible`, `exec_reason_flags`|E_execの当時の判定。後から再ラベルしない|
|`analysis_eligible`, `analysis_reason_flags`|E_analysisの観測可用性。取引判断に流用しない|
|`outcome_status`|KNOWN_NO_TRADE / KNOWN_PNL / UNKNOWN_PNL / OPEN_POSITION 等|
|`net_pnl_jpy`|既知値だけ数値。不明はnull。既知無取引は0、費用あり取消は費用分を反映|
|`spec_version`, `family_id`, `study_id`, `run_id`|仕様・探索群・研究・技術試行を区別|
|`estimand_id`, `unit`, `denominator_axis_id`|推定対象、円/取引か円/日か、固定分母|

`event_definition_id`は不変とし、選択・注文・約定statusを別表へ分ける。nullの値を0、false、空文字で置き換えない。未知品質は研究ゲートで判断し、仮定を観測事実へ格上げしない。

### Required reconciliation

各conditionで、固定予定軸の件数が既知無取引・既知損益・不明・予定上対象外の排他的内訳と一致することを検査する。台帳の件数・fees・Gross・Netと集計を照合し、円/日と円/取引の違いをassertする。主軸にUNKNOWN_PNLがあれば完全Net・Sharpe・CIを未確定とする。

費用還元は同一reference経路が証明されたときだけ診断に使う。標準の1枚・片道1tick/30円のsynthetic round tripでNet=−1,060円となる例を維持する。その他cost項目がある場合は別に列挙する。
