# 135. C05 R103 昼休み確認設計の閉鎖監査

タスク: **TASK-C05-R103-DESIGN-CLOSURE-09** / 2026-09-17 JST

## 目的

C05候補として保留されていたR103/Q001について、既存のPnL前情報量停止を正しく固定し、同じ件数不足の再発見や事後的な救済探索を止める。これはR103の収益性を評価する仕事でも、F07を再開する仕事でもない。

R103は2021初期化部分のA完了4件が、事前固定下限8件に届かなかったため、PnL前に停止した。したがってPnLは`NOT_OBTAINED`、経済性は`NOT_EVALUATED`である。F07のR081等にある経済的`REJECT`と混同しない。

## 読み取り範囲

最初に`AGENTS.md`、00、13、120、121、123、124、registry、`config/research_execution.json`、R103の[仕様](118_r103_q001_lunch_cash_confirmation.md)と[結果](119_r103_q001_lunch_cash_confirmation_result.md)を読む。すべて読み取り専用である。公開調査、Web検索、データファイル、`results/research/`、価格又はPnLの保存物は開かない。

## 禁止事項

- `data/raw/`、正規化Parquet、派生バー、価格cache、取引明細、特徴量、`results/research/`を開かない。
- PnL、return、PF、orders、fills、trades、年別損益、感度、OOS、Final Holdoutを取得、計算、表示しない。
- R103/F07のfamily、例外、有限attempt、budget、manifest、ReviewReceipt、grant、engine、既存のregistry又は結果を作成・変更しない。
- 4件に合わせて8件の下限を下げず、窓、閾値、entry、exit、方向、比較、期間を変更した再設計を作らない。既存Developmentを新しい未使用標本として扱わない。

## 実施内容

1. `prior_contract.md`で、R103の凍結仕様・停止理由・PnL未取得・経済性未評価を根拠への相対リンクと共に整理する。R081等の経済的REJECTは、数値を引用せず状態の違いだけを記録する。
2. `sample_design.json`で、`task_id`、`study_id`=`R103/Q001`、`family_id`=`F07`、`current_design_decision`=`CLOSE_CURRENT_DESIGN`を固定する。`frozen_2021_initialization_minimum`=8、`observed_2021_initialization_count`=4、`pnl_evidence`=`NOT_OBTAINED`、`economics_status`=`NOT_EVALUATED`とする。`family_closure`には残り経済仕様・パラメータ枠0と`automatic_reopen=false`を置く。`market_data_accessed`、`pnl_accessed`、`authorises_family_reopen`、`authorises_market_data_access`、`authorises_execution`、`authorises_exception`は全てfalseとする。`exception_path`は`SEPARATE_HUMAN_AUTHORIZATION_REQUIRED`、`exception_created=false`とする。
3. `synthetic_count_cases.json`に、次の固定ケースを置く。全ケースは`authorises_real_access=false`で、理由を持つ。
   - `observed_four_vs_min_eight` → `FAIL_INFORMATION_GATE`
   - `lower_minimum_after_shortfall` → `PROHIBITED_POST_HOC`
   - `new_full_period_design` → `SEPARATE_HUMAN_EXCEPTION_REQUIRED`
   - `economic_reject_neighbour` → `PRESERVE_ECONOMIC_REJECT`
   - `pnl_before_shortfall` → `NOT_EVALUATED`
   - `same_family_variant` → `CLOSED_NO_AUTOMATIC_REOPEN`
4. `verification.md`と`review.md`で、停止理由、禁止事項、固定validator、実アクセス0、変更範囲を監査する。市場実行や自動例外を提案しない。

## 成果物

`docs/strategy/plans/TASK-C05-R103-DESIGN-CLOSURE-09/` にだけ、`prior_contract.md`、`sample_design.json`、`synthetic_count_cases.json`、`verification.md`、`review.md`を作る。

## 固定検証と終了

```powershell
.venv/Scripts/python.exe scripts/validate_c05_r103_design_closure.py --artifacts docs/strategy/plans/TASK-C05-R103-DESIGN-CLOSURE-09
```

validatorは成果物構造、既存の8/4件数、状態区別、閉鎖境界、固定合成ケースを検査する。構造PASSはR103の経済性、再開、実行、OOS、Final Holdoutへの許可を意味しない。

資料の記録が矛盾する場合は証拠と矛盾を残して`BLOCKED`とする。既存の範囲だけで閉鎖判断を確認できれば`DONE — CLOSE_CURRENT_DESIGN`で終了する。いずれの場合も実データ、OOS、Final Holdout、F07再開を行わない。
