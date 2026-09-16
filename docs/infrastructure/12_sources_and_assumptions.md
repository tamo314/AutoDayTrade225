# 12. Sources, Verified Facts, and Assumptions

Historical external-source review recorded in the original: 2026-09-13 JST.
Document revision: RG-20260915-01, 2026-09-15 JST; external sources not reverified in this revision.

## Primary sources

### JPX — Nikkei 225 mini contract specification

https://www.jpx.co.jp/derivatives/products/domestic/225mini/01.html

Facts recorded as verified by the original document (not reverified in this revision):
- day opening 08:45
- regular session through 15:40, closing auction 15:45
- night opening 17:00
- regular session through 05:55, closing auction 06:00
- contract unit Nikkei 225 × 100 JPY
- tick size 5 JPY

### JPX — trading hours

https://www.jpx.co.jp/derivatives/rules/trading-hours/index.html

Used for current auction/session detail.

### JPX — J-GATE3.0 launch, 2021-09-21

https://www.jpx.co.jp/corporate/news/news-releases/0060/20210921-01.html

Verified: night session was extended to 06:00 on 2021-09-21.

### JPX — 2016 J-GATE history

https://www.jpx.co.jp/news/0060/20170719-01.html

Verified: on the 2016 J-GATE renewal, index futures day opening moved from 09:00 to 08:45 and night session close extended to 05:30. This supports the pre-2021 schedule used for the initial 2021 history.

### JPX — 2024-11-05 trading-hours changes

https://www.jpx.co.jp/english/corporate/news/news-releases/1030/20241103-01.html

Verified: derivatives trading hours changed on 2024-11-05 in conjunction with the cash-market trading-hours extension.

### 225Labo — Nikkei 225 mini data page

https://225labo.com/modules/downloads_data/index.php?cid=3

Verified:
- minute data available from 2006
- 1, 3, 5, 10, 15, 20, 30, 60-minute intervals
- from 2011-02-14 the provided center series is a continuous series using the same contract month as the large Nikkei 225 futures center/near contract concept described by 225Labo
- registered members may download data for free
- redistribution/third-party provision is prohibited by the site terms shown on the page

### 225Labo — example center-series explanation

https://225labo.com/modules/downloads_data/index.php?cid=3&lid=115&page=singlefile

Verified:
- the mini center series can differ from simple nearest-month mini because it follows the contract month corresponding to the most-active/large-futures center convention described by 225Labo
- night-session dates follow the Osaka Exchange date convention and are shown as the next business/trading date

## Assumptions requiring validation when actual files are available

1. Exact 225Labo CSV column names/order/encoding are not frozen in this package.
2. Availability/meaning of volume in each downloadable file must be inspected.
3. Whether source bar timestamps label bar start, bar end, or auction minute must be verified from actual files/site documentation before relying on minute-level boundary semantics.
4. Exact exchange holiday-trading calendar should be supplied/generated from authoritative JPX data for full historical reconstruction.
5. A center-series rollover marker is not assumed to exist in source data. Keep `roll_observation_status=unknown` and unobserved changes null; an existing false bool or an SQ/calendar proxy is not evidence that no actual roll occurred.

## Design response to uncertainty

The adapter and calendar components must expose uncertainty rather than silently guess. Actual source inspection is an onboarding step after code is implemented.

## RG-20260915-01: 証拠の版・適用範囲

上記の外部URL・制度説明・確認日は旧文書から保持した記録である。本改訂では外部サイトを再取得しておらず、文書制定日を新たな外部検証日として使わない。URLが存在すること、現在の制度が分かること、対象の過去データの意味が説明できることは別である。

### Evidence matrix

|項目|必要な根拠|不足時の扱い|
|---|---|---|
|時刻ラベルと利用可能時刻|対象ファイル版のbar-start/end/auction規約、確定時点、訂正方針|境界を使うPnLを止める。モデル時刻を観測事実にしない|
|価格種別|通常立会・別市場・清算・加工研究価格の区別|5刻み検査だけで約定可能価格としない|
|中心限月構成・roll|対象日時の選択限月、切替有効時刻、調整方式、利用可能時刻|unknown/nullを維持。SQ予定ラベルは実切替の代用品にしない|
|出来高|各1分の確定約定数量、単位・非累積、zero/missing/訂正|列mappingだけでVWAP等へ利用しない|
|OSE/TSE予定表|対象期間の制度・取引日・現物営業日・休日と版hash|観測データの行だけで休業や予定を確定しない|
|費用・遅延|ユーザー固定研究設定と、必要なら別途実測根拠|片道1tick/30円は固定モデル。実測した値と称さない|
|実装済み機能|対象code snapshot、実測test/help/ledger|文書の名称だけで実装済みとしない|

各証拠に `evidence_id`、source、対象期間、対象file/hash、確認した命題、確認していない命題、取得日、適用版、available_atの根拠、review_statusを保存する。今回の雛形は空欄・UNKNOWNを含み、監査完了ではない。

### 本改訂の追加規約の出所

[13_research_governance.md](../strategy/13_research_governance.md)の四集合、有限バッチ、多軸判定、段階移行は、ユーザーが依頼した研究手順の修正として追加した設計判断である。元資料に書かれていた実績や市場の実証事実として扱わない。

[14_research_repair_plan.md](../strategy/14_research_repair_plan.md)はR031の矛盾、R046等の集合依存、R032の数値不一致を原文行へ紐付ける。実コード・実データ・取引台帳が未提供のため、影響金額や正しい再集計値は確認していない。
