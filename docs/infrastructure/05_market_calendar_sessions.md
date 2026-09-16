# 05. Market Calendar and Session Rules

## 1. Current N225M exchange specification

Current JPX specification (verified 2026-09):
- Day opening auction: 08:45
- Day regular session: 08:45–15:40
- Day closing auction: 15:45
- Night opening auction: 17:00
- Night regular session: 17:00–05:55
- Night closing auction: 06:00
- Contract unit: Nikkei 225 × 100 JPY
- Tick size: 5 JPY

## 2. Historical regimes needed for the initial 2021+ dataset

The configuration uses coarse session boundaries plus auction metadata.

### Regime A — through 2021-09-20

- Day session overall: 08:45–15:15
- Night session overall: 16:30–05:30

Relevant history: the 2016 J-GATE renewal moved index futures day opening to 08:45 and extended night close to 05:30.

### Regime B — 2021-09-21 through 2024-11-04

- Day session overall: 08:45–15:15
- Night session overall: 16:30–06:00

On 2021-09-21 J-GATE3.0 extended the night session to 06:00.

### Regime C — from 2024-11-05

- Day session overall: 08:45–15:45
- Night session overall: 17:00–06:00
- Day regular session ends 15:40; closing auction 15:45
- Night regular session ends 05:55; closing auction 06:00

JPX changed derivatives trading hours in conjunction with the cash market extension on 2024-11-05.

## 3. Important implementation note

The ranges above describe exchange session boundaries, not a promise that every minute has an OHLC record. Auctions, no-trade minutes, interruptions and source conventions can create gaps.

Do not pre-generate bars and fill them. Instead, generate an expected-session minute grid for quality analysis and compare source bars against it.

## 4. Trade date model

`trade_date` is the exchange trading date that groups the preceding evening/night session and the day session according to OSE rules.

For 225Labo, the source states the night-session date follows the OSE convention and uses the next business/trading date. Therefore actual timestamps must be reconstructed using an exchange calendar relation.

## 5. Exchange calendar table

Implement a persisted table with at least:

| field | meaning |
|---|---|
| `trade_date` | OSE trading date |
| `previous_trade_date` | prior OSE trading date |
| `next_trade_date` | next OSE trading date |
| `night_calendar_start_date` | actual date on which evening night session starts |
| `is_holiday_trading_day` | whether a holiday trading session exists |
| `schedule_version` | regime id |

The system should allow an explicit CSV/YAML calendar override supplied by the user.

At a regime boundary, day-session rules are selected by `trade_date`, whereas
night-session rules are selected by `night_calendar_start_date`. This preserves
the actual schedule of a session that starts before a rules change and ends on
its first trade date after that change.

## 6. Holiday trading

OSE derivatives holiday trading began in 2022. Holiday sessions may affect the relationship between Japanese public holidays, calendar days, and trade dates. Do not use a generic Japanese bank-holiday calendar as a substitute for the exchange calendar.

If the calendar is incomplete, classify the uncertainty as a quality/error condition rather than guessing.

## 7. Forced-flat configuration

Entry/force-exit times are strategy/backtest policy, not exchange rules. Store them separately in `backtest.yaml` and validate them against the effective session schedule.

## 8. 予定表・観測時点の補足 — RG-20260915-01

冒頭の市場制度値と外部検証日は原文の記録を継承したもので、本改訂で外部再確認したものではない。対象期間の公式制度証拠と、データ供給者のbarラベル／auction収録方法は別に検証する。

取引日対応は予定表の実時間順で固定し、nightの適用版はnight_calendar_start_dateを基準にする。深夜以後をtrade_dateの日付と仮定せず、そのnightの実開始・終了区間から導く。予定表が不明なら推測で埋めない。

OSE取引日とTSE通常営業日・前後場境界を別表で扱う。元ファイルの日付集合から作ったローカルcalendarは観測カバレッジの候補であり、完全な公式予定表、休業日、欠落sessionの不存在の証明ではない。制度版・取得元・hash・有効期間を保存する。

予定軸の対象外は、期間・制度・対象市場など価格非依存で先に決める。実際に後刻の足がなかったことを予定休場へ読み替えない。後日のcalendar訂正は、元の知識と訂正後の分析を別versionで管理する。

R065等はn0→d0→n1の対応、週末／祝日休場、通常取引・auction、許可期間終端を [R065レビュー](../strategy/15_r065_execution_review.md) で確認する。価格を使わずに予定上期間外と判定できる条件は事前除外できるが、将来の約定可否で過去の母集団を変更しない。
