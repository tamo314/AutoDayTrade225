# C04 BLS Employment Situation: source contract

Task: `TASK-C04-EVENT-CALENDAR-AUDIT-08`  
Audit date: 2026-09-17 JST  
Scope: one BLS publication, **The Employment Situation**, and only its official release-calendar and timestamp provenance. This is not an economic-value, surprise, market-response, signal, execution, or PnL review.

## Bounded public-research log (cumulative)

All queries and source bodies below are the complete public-research use for this task: four search queries and seven BLS-owned HTML bodies, within the respective limit of eight. No individual Employment Situation release, PDF, data table, download, economic-calendar aggregator, price page, time series, secondary article, or social-media post was opened or used.

| ID | Query / action | Outcome | Count |
|---|---|---|---:|
| Q01 | `site:bls.gov/schedule "Employment Situation" "2021" "8:30"` | Located the BLS annual release-calendar route. | query 1 |
| Q02 | `site:bls.gov/schedule "Employment Situation" "2022" "2023" "2024" "2025"` | Confirmed the same official annual-calendar route for the target span. | query 2 |
| Q03 | `site:bls.gov "Employment Situation" "release schedule" "8:30 a.m."` | Located BLS dissemination policy and the archive index. | query 3 |
| Q04 | `site:bls.gov "Employment Situation" revisions "news release"` | Individual release-body candidates were outside the value-free scope and were not opened or used; retained only as an exclusion record. | query 4 |
| BLS-S01 | Opened the official [2021 schedule](https://www.bls.gov/schedule/2021/). | Calendar contains scheduled release dates/times and labels calendar times Eastern Time. Its monthly last-modified metadata varies (observed on the source). | body 1 |
| BLS-S02 | Opened the official [2022 schedule](https://www.bls.gov/schedule/2022/). | Same scheduled-calendar and Eastern-Time evidence. | body 2 |
| BLS-S03 | Opened the official [2023 schedule](https://www.bls.gov/schedule/2023/). | Same scheduled-calendar and Eastern-Time evidence. | body 3 |
| BLS-S04 | Opened the official [2024 schedule](https://www.bls.gov/schedule/2024/). | Same scheduled-calendar and Eastern-Time evidence. | body 4 |
| BLS-S05 | Opened the official [2025 schedule](https://www.bls.gov/schedule/2025/), using only the portion through 2025-06-30. | Same scheduled-calendar and Eastern-Time evidence; it is not used for any OOS date. | body 5 |
| BLS-S06 | Opened the official [Employment Situation archive index](https://www.bls.gov/bls/news-release/empsit.htm), last modified 2026-09-04. | Identifies the Employment Situation archive, years including 2021--2025, and cautions that archived release data may later be revised. The index itself does not provide an actual-release timestamp field. | body 6 |
| BLS-S07 | Opened BLS [Dissemination](https://www.bls.gov/about-bls/dissemination.htm), last modified 2026-08-28. | Names Employment Situation among the PFEI releases; says PFEI dates/times are published in advance and scheduled at 08:30 Eastern Time; says rare technical delays can occur. It does not supply a historical scheduled-versus-actual ledger. | body 7 |

All sources were retrieved on 2026-09-17 JST. Source dates above mean page metadata where shown, not a claim that a source was available at a historical decision time. The schedules' per-month revision metadata means they cannot be treated as immutable, contemporaneous calendar snapshots.

## Claim ledger

| Claim | Status | Supporting sources | Does **not** establish |
|---|---|---|---|
| The audited object is one BLS Employment Situation release series, not a bundle of indicators. | supported | BLS-S06, BLS-S07 | an event-derived C04 rule or a usable input row |
| BLS publishes a calendar in advance and identifies Employment Situation as scheduled at 08:30 Eastern Time. | supported as a schedule convention | BLS-S01--BLS-S05, BLS-S07 | that each target-period occurrence was actually published at the scheduled instant |
| Scheduled dates/times can be read from the annual calendar pages for 2021-01-01 through 2025-06-30. | supported only as currently displayed scheduled metadata | BLS-S01--BLS-S05 | an as-of historical calendar version, actual publication, cancellation, delay, or emergency change status |
| An official archive exists for Employment Situation and BLS warns that archived release data can be revised. | supported | BLS-S06 | a rule that a revision or republication preserves, replaces, or otherwise identifies the original event timestamp |
| Delays are possible in rare technical circumstances. | supported as a general policy statement | BLS-S07 | whether any target-period schedule changed, and the actual substitute timestamp |
| ET-to-JST conversion, including daylight-saving determination, is documented by BLS for every target event. | unsupported / unverified | none | a permitted JST timestamp conversion |
| OSE session and `trade_date` alignment is documented by the allowed BLS-only source set. | unsupported / unverified | none | a session eligibility or `trade_date` assignment |

## Attribute findings and fail-closed consequence

The BLS-only material establishes identity and scheduled-clock convention, but it does not establish the three conditions needed for a future post-release event input: an actual target-period timestamp distinct from the schedule, a documented ET/DST-to-JST conversion, and an OSE session/`trade_date` rule. It also does not establish the requested revision/republication timestamp semantics. The task therefore records `NOT_USABLE_FOR_C04`, rather than deriving a timestamp or silently substituting the schedule.

No target-period event-date list is copied into these artifacts. Doing so would turn the scheduled calendar into an unapproved input despite the missing actual-status and timezone/session evidence.

## Separation from preserved research context

- **F04** is an already closed local-shock family. This audit neither reopens it nor compares its results with C04.
- **F13/R082** is an already closed U.S.-cash-open family. Its clock-related history is not evidence of an Employment Situation release timestamp or of C04 eligibility.
- **C03** remains `NOT_VERIFIED` for its separate USDJPY public-input audit. That finding supplies neither calendar evidence nor a C04 input.

None of these context records was modified or used to infer a BLS timestamp, market result, or C04-family authority.
