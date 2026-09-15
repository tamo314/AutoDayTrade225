# TASK-R095-Q001: 現物昼休み中の極端先物変化の後場再開追随 — 重複事前照合

記録日: 2026-09-15 JST  
run_id: `task-r095-q001-tse-lunch-extreme-follow-duplicate-20260915-01`  
状態: **STOPPED_AS_MATERIALLY_EQUIVALENT_BEFORE_NEW_PRICE_OR_PNL_ACCESS**

## 固定依頼の照合範囲

本記録は、依頼されたR095-Q001のDevelopment価格、event件数、orders、fills、trades、return、PnL、PF、bootstrap、感度を新規に作成又は取得する**前**に、R001--R094の研究記録を照合したものである。Developmentは既に反復利用済みであり、入力は225Labo由来の正規化連続系列に限定されることも、開始前の制約として記録する。OOS、Walk Forward、2026-01-01以降のFinal Holdoutにはアクセスしない。

調査は`docs/strategy/03_experiment_plan.md`、`04_research_results.md`、R070以降の個別登録・結果、及び対応実装・テストの識別子を対象にした。昼休み関連の直接候補はR020、R029、R038、R045、R081、R084、R090であり、R038とR081を詳細比較した。R020/R029/R045/R090は反転、確認、placebo又は再開後の否定を含み、R084は午前圧縮と午後breakoutを使うため、R095の直接同値候補ではない。

## 直接重複の判定

**R038-Q001** (`r038-q001-20260914-tse-lunch-extreme-follow-04`) は、次をすでに一回評価している。

| 項目 | R095依頼 | R038-Q001 |
|---|---|---|
| 制度的仮説 | 現物昼休み中の先物価格発見が再開後に同方向へ継続 | 現物昼休み中の極端な先物変位が再開後に同方向へ追随 |
| 変位窓 | 11:30 open → 12:29 close | 11:30 open → 12:29 close |
| 方向 | `sign(l)` | `sign(rL)` |
| 強度 | current-excluded rolling q75 | current-excluded rolling q75 |
| entry / exit | 12:30 open / 13:00 open | 12:30 open / 13:00 open |
| 実行 | 1枚、最大1、Stop/Target/re-entryなし | 同じ |
| 基本費用 | 片道1 tick + 30円 | 同じ |

R095がR038から変えるのは、参照予定日を60日・有効50件から120日・有効100件へ延長すること、`>`を`>=`へ変えること、及び対照・ゲートの詳細である。これらは既存の同じ経済機序・同じ価格窓・同じ売買方向・同じentry/exitに対する閾値履歴／判定設計の差である。研究統治書13章の「窓、分位点、方向、保有時間、比較式の差だけで新しい独立familyを主張しない」に従い、R095をR038と**materially equivalent**と判定する。

R081-Q001も同じ昼休み変位の同方向追随を12:30から実行しており、R038の直接重複判定を補強する。ただしR081は全nonzero変位・14:30 exitなので、単独ではR095の最も直接の同値根拠ではない。

## 停止決定

依頼の「materially equivalentな既存実験があればPnLを取得せず重複として停止する」を適用する。R095は新規のコード、設定、価格読込、予定表展開、非PnL可用性集計、注文、約定、PnL、bootstrap又は感度を実行しない。R038の保存済み結果をR095の結果として再集計・再利用もしない。

この停止はR038の既知結果に基づく閾値・方向・entry/exitの救済変更ではなく、実質同一のDevelopment再評価を防ぐための統治上の決定である。R095の判定状態は`DUPLICATE_STOPPED`であり、REJECT/INVESTIGATE/CANDIDATEの経済判定ではない。
