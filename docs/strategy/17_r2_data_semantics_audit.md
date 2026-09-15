# 17. R2 データ意味・価格来歴監査

監査ID: **AUDIT-R2-01-20260915T025000Z**  
規約: **RG-20260915-01**  
状態: **PASS_LIMITED — owner accepted recorded unknowns**  
運用状態: **PAUSED_METHOD_REPAIR を維持**

## 結論

R2の証拠収集・判定監査は完了している。供給者仕様が得られない事実を記録したうえで、所有者の明示的なリスク受容により、品質gateを`PASS_LIMITED`へ変更した。これは供給者の意味を証明したPASSではない。限定したDevelopmentのデータ品質・固定仕様診断は続行できるが、OOS、Final Holdout、候補選定、執行主張は引き続き禁止する。市場価格、出来高、特徴量、過去run、OOS、Final HoldoutはこのR2監査では読んでいない。

225Laboの公開案内からは、日経225ミニに1分間隔のデータがあること、2011-02-14以後を日経225ラージに合わせた取引中心限月の連結系列として説明していることだけを記録した。[225Laboのデータ案内](https://225labo.com/modules/downloads_data/index.php?cid=3) これは構成上の手掛かりであり、対象ファイルのbar時刻・OHLC・出来高・roll・調整・可用時刻を証明するものではない。

## 実測した範囲

- `config/data.yaml` の `225labo`、`center_continuous`、`1m`、列マッピング、trade-date宣言を記録した。
- `source_semantics_evidence.yaml` の公開説明を一件だけ根拠として保存し、bar間隔以外を満たしたものとして扱わなかった。
- ingest manifest、quality report、pipelineのコードを確認した。これらは構造QC、hash、設定宣言を保存するが、供給者のbarラベル、OHLC構成、出来高単位、訂正、契約構成、roll、調整、公開時刻を立証しない。
- `research audit-data-semantics` は上記の非価格入力だけを読取り、排他的な監査出力を生成する。価格や既存キャッシュを入力にできない。

## 未解決の重大事項

|事項|状態|R2上の扱い|
|---|---|---|
|bar開始／終了ラベル、JST・trade-date境界|不明|記録済みリスク|
|OHLCの構成、未約定bar|不明|記録済みリスク|
|出来高単位・各bar非累積性|不明|記録済みリスク|
|欠損、訂正、履歴改訂・品質ラベルの可用時刻|不明|記録済みリスク|
|実限月、切替観測、roll規則|不明|記録済みリスク|
|連結時の価格調整|不明|記録済みリスク|
|価格・出来高・品質のdecision-time可用時刻|不明|記録済みリスク|

`causal_quality_audit.json` は品質情報の時点可用性を証明できないためBLOCKED、`outcome_missingness.json` は価格・outcomeを開かない監査であるためNOT_RUNである。これらを品質PASSや損益の結果に転記しない。`PASS_LIMITED`は、証拠不足を不可視化せず、限定診断の停止要因から外すための意思決定記録である。

## 次に必要な権限・証拠

供給者または保持者の対象ファイルに対応する書面の仕様、もしくは同じ期間を網羅し必要仕様を示す代替データ候補の採否判断は、引き続き望ましい。外部連絡、購入、ダウンロードは本監査では行っていない。未送信の照会文案は監査成果物にのみ保存した。

本リスク受容は、source semanticsに起因し得る異常がDevelopmentデータ監査で現れた時点で再審査する。根拠が揃えば、R2は用途別に再判定する。`PASS_LIMITED`は限定Development診断までであり、OOS候補や実行可能性の主張には進まない。R3-A、R065専用executor、OOSを自動開始しない。
