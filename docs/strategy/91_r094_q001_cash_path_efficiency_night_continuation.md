# R094-Q001: TSE cash path efficiency to following OSE night continuation

Task ID: `TASK-R094-Q001`  
Status: `FROZEN_BEFORE_PNL`  
Scope: Development only, `trade_date` 2021-01-01 through 2025-06-30. The decision ceiling is **INVESTIGATE**.

## Prior-information and novelty record

This is a post-R093 hypothesis registered after repeated use of the Development sample; it is not an independent confirmation and may not be promoted beyond INVESTIGATE. Before any R094 price, event, fill, return, PnL, PF, bootstrap, or sensitivity access, R001--R093 were checked. Known related outcomes are retained without alteration: R009's short in-session path-efficiency continuation was INCONCLUSIVE for insufficient sample; R046's opening path-efficiency continuation was REJECT and has `POSTHOC_UNIVERSE_CONDITIONING` use limitation; R079's first-hour extreme continuation is a prior Development result; R088's cash-open path efficiency stopped INCONCLUSIVE for control availability; R089's late concentration continuation was REJECT; R093-Q002's whole-cash-to-following-night reversal was REJECT. This is not a selection of R093's continuation point estimate.

The distinct hypothesis is: **on an official TSE cash day, a sufficiently large one-direction, efficiently formed full cash-session displacement continues in the corresponding official OSE night session after costs.** It uses only the normalized 225Labo-derived continuous Parquet series. It neither invents an actual contract nor claims live tradability. `data/raw/`, OOS, Walk Forward, and the 2026+ Final Holdout are excluded.

## Frozen causal rule

For each official TSE business day `d`, use the versioned normal cash schedule: 09:00--11:30 and 12:30--15:00 through 2024-11-01; 09:00--11:30 and 12:30--15:30 from 2024-11-05. Every scheduled one-minute cash bar must be eligible, positive, and unique. Let `O` be the 09:00 scheduled bar open, `C` the close of the final scheduled bar before cash end `T`, and

`r=(C-O)/O`, `x=abs(r)`, `V=abs(close_09:00-O)+sum(abs(close_i-close_(i-1)))` over the subsequent scheduled cash bars in chronological order, and `e=abs(C-O)/V`.

The sequence joins the 11:29 close to the 12:30 close once and only once. Require `V>0` and `r!=0`; no bar-derived schedule, close-after-`T` price, cash-to-night gap, weekday, Stop, Target, re-entry, or intraday update may be used.

At position `i` on the official TSE-day axis, references are exactly `axis[max(0,i-120):i]`, ordered, unique, current-excluded and never backfilled. Valid nonzero `x/e` observations in that exact window are used; q-ready requires all 120 scheduled dates and at least 100 valid observations. Nearest rank is `ceil(n*p/100)-1`. Compute `qx50`, `qx40`, `qx60` on `x`, and `qe70`, `qe75`, `qe80` on `e` from the same current-excluded valid observations. Equality at `qe` belongs to the high-efficiency state.

Primary H is `x>=qx50 and e>=qe75`; K is `x>=qx50 and e<qe75`. H and K are decided entirely at the cash close. The rolling x-percentile used only for the pre-registered A--M comparison is `count(reference_x <= current_x)/n`; bands are `[0.50,0.75)` and `[0.75,1.00]` (upper endpoint included only in the latter).

H submits at `T`, following cash signal confirmation, in direction `sign(r)`. The explicit one-to-one calendar mapping is from cash day `d` to the following OSE night whose `night_calendar_start_date=d`. It fills one contract at that night's first normal scheduled bar open and exits at the open of the last normal scheduled minute bar. The new closing-auction portion is excluded from normal bars. H/K state is never selected by future-night availability. A filled entry with unavailable exit is unresolved/null and stops before PnL.

A is H continuation; B is same-H reversal; C/D are same-H fixed long/fixed short; M is K continuation with exactly the same scheduling; N is zero-JPY no trade. A/B/C/D share H, submit, entry, exit and one-contract/maximum-one-position constraints. Costs are one adverse tick plus JPY30 each side, with `Net=Gross-fees` and no double deduction of slippage.

## PnL-free gate

All conditions must pass before economic computation: zero unexplained exclusions; zero filled unresolved exits; at least 800 q-ready nonzero days; at least 100 completed H; H positive and negative cash directions at least 35 each; at least 250 completed K; H 2021 initialization at least 8, each 2022--2024 at least 18, 2025H1 at least 8; and old/new OSE regimes at least 85/8. Audit planned TSE axis, old/new cash/OSE regimes, mapping, `trade_date`/calendar date, R004 isolation, post-signal submit, maximum position, accounting, and no future-night H/K selection.

## Fixed analysis and decision

After the gate only, run `qe70`, `qe80`, `qx40`, `qx60`, one-minute entry delay, 30-minute early exit, two and three adverse ticks, and double fees. Run non-circular 20-`trade_date` moving-block bootstrap, 10,000 repetitions, seed `20260915`, tail truncation, linear percentile, with common indices. Evaluate A daily Net; paired A-B/A-C/A-D daily Net; and A-M conditional Net-per-trade. For A-M, each band requires H/M at least 20 trades; within each bootstrap draw take H mean Net minus M mean Net in each band, then equally weight the two differences. This equal-weight average is the registered standardized A-M difference.

The primary AND is: A Net>0; PF>1; A daily-Net CI lower>0; each paired A-B/A-C/A-D CI lower>0; and standardized A-M CI lower>0. Any failure is **REJECT**. A primary pass is still only INVESTIGATE unless every fixed sensitivity has Net>0; both cash signs and both OSE regimes have Net>0; at least two years from 2022--2024 and 2025H1 are positive; and removing the ten largest winning A trades leaves Net>0. Save three-tick as a mandatory diagnostic. Do not change efficiency/displacement thresholds, direction, entry/exit, night interval, run Walk Forward, inspect 2025H2 OOS, or inspect Final Holdout.
