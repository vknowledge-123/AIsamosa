# Heuristic SL Hunting Rulebook v5

Source basis:

1. `numberical SL hunting by Amol.txt`
2. `transaction SL enginners.txt`
3. `tracipt of mr amol for SL hunting.txt`
4. `chat SL 1.txt`
5. `chat SL 2.txt`
6. `chat_SL 3.txt`
7. `chat_sl 4.txt`
8. `chat Sl 5.txt`
9. `chat 6.txt`
10. `chat SL 7.txt`
11. `chat SL 8.txt`
12. `chat SL 9.txt`
13. `chat SL 10.txt`
14. `chat SL 11.txt`
15. `chat SL 12.txt`
16. `chat 13.txt`

This v5 rulebook keeps the v4 operating discipline and adds the next meaningful upgrades from the latest documents:

1. previous close is promoted into a primary participation and confusion level
2. opening type is interpreted through previous-day structure, not in isolation
3. liquidity levels are tiered so the engine stops treating every swing as equally important
4. reclaim quality is scored instead of guessed
5. market bias generation is separated from running-trade management
6. day types are expanded so the system can distinguish trend, trap, grind, and confusion regimes more reliably

This is the first version that behaves like a full heuristic trading framework rather than only a pattern library.

## 1. Core belief

Market moves toward available stops and trapped positioning, not toward our opinion.

The best intraday trade usually appears after:

1. important liquidity is mapped
2. opening context identifies who is vulnerable
3. market sweeps or pressures that vulnerable side
4. price either accepts the move or rejects and reclaims it
5. the day type confirms whether continuation, reversal, or double-side farming is more likely

## 2. Priority hierarchy

When signals conflict, follow this order:

1. opening type plus previous-day context
2. psychology and stop availability
3. liquidity hierarchy
4. writer comfort and writer pain
5. acceptance vs rejection
6. live regime or day type
7. structure
8. execution trigger

Do not let a clean 1-minute candle override a stronger opening or liquidity context.

## 3. Market inputs

The engine should read:

1. previous day high, low, and close
2. current day 1-minute candles from open
3. first 5-minute range
4. first 15-minute range
5. session high and low
6. previous session swing highs and lows
7. round numbers in `00` and `50`
8. weekly option-chain strikes near spot
9. monthly option-chain strikes near spot
10. premium and premium change at top OI strikes
11. fair-value proxy such as VWAP or session POC
12. relative behavior of Nifty, Bank Nifty, and Sensex
13. gap size and opening location
14. expiry, Friday, or holiday context
15. current running-trade state if any
16. time-of-day bucket
17. previous-day structure label
18. opening type label

## 4. Timeframe hierarchy

Timeframe itself does not create edge. Price movement quality does.

### 4.1 Timeframe usage

1. higher timeframe gives context
2. lower timeframe gives entry
3. breakout strength depends on displacement and acceptance, not on the label `1m` or `5m`

### 4.2 Practical use

1. use `15m-1h` for broad intraday bias
2. use `5m-15m` for structure context
3. use `1m` for sweep, reclaim, retest, and execution timing

## 5. Liquidity hierarchy

Not all levels carry equal stop value.

### 5.1 Tier 1 liquidity

Treat these as primary:

1. previous day high
2. previous day low
3. previous day close
4. previous session major swing highs or lows
5. equal highs or equal lows near obvious session extremes
6. opening-range high and low

### 5.2 Tier 2 liquidity

Treat these as useful but secondary:

1. round numbers such as `00` and `50`
2. intraday equal highs or equal lows
3. fair-value zones such as VWAP or session POC
4. local structure shelves repeatedly defended during the session

### 5.3 Tier 3 liquidity

Treat these as weak unless confirmed by context:

1. random micro highs or lows
2. isolated single-candle pivots with no crowd attention
3. minor pauses inside a strong trend leg

### 5.4 Rule

When a Tier 1 and Tier 3 level conflict, assume the Tier 1 level matters more unless strong acceptance proves otherwise.

## 6. Liquidity persistence

Price movement does not automatically mean traders exited.

### 6.1 Strong move vs weak move

1. strong move means the trapped side is more likely removed
2. weak move means the trapped side may still be holding and remains future fuel

### 6.2 Strong move definition

Treat move as strong if one or more are true:

1. three same-direction 1-minute candles expand with momentum
2. directional move exceeds about `0.35%` within `15` minutes
3. retracements are shallow and quickly absorbed

If these are absent, do not assume liquidation happened.

## 7. Writer comfort and pain

### 7.1 Writer comfort

Writer comfort exists when:

1. price stays inside expected range
2. premiums decay without panic
3. breakout attempts fail quickly
4. fair-value trading dominates

### 7.2 Writer pain

Writer pain exists when:

1. an important strike moves quickly ITM
2. premium expansion is sudden
3. a level that looked safe is breached with displacement
4. follow-through appears before decay can normalize

## 8. Gap and opening logic

Gap is setup, not signal. Opening type decides who can be the target.

### 8.1 Gap rules

1. flat or mild gap often creates the cleanest stop-hunt opportunity
2. direct one-sided open is dangerous because reward may already be spent
3. large gap of roughly `200-300` points requires reset logic
4. very large gap requires waiting `15-30` minutes before trusting old levels

### 8.2 Opening pullback advantage

Prefer days where opening move first shakes out the wrong side and only then expands.

### 8.3 One-sided open warning

If market opens and immediately runs in one direction without pullback:

1. do not assume continuation is easy
2. mark trap risk
3. wait for either acceptance or failure

## 9. Previous-day structure classification

Before market open, classify the previous day into one of these buckets:

1. strong bullish continuation day
2. bullish recovery day
3. strong bearish continuation day
4. bearish cleanup day where sellers likely already exited
5. sideways or distribution day
6. confusion day with no dominant side

This classification becomes the base context for the opening bias engine.

## 10. Opening-type bias engine

Opening type must be read through previous-day structure.

### 10.1 After a bullish recovery or strong bullish day

If today opens:

1. flat or mild gap up, default watch is buy-side continuation after early shakeout
2. small gap down, default watch is whether the dip gets reclaimed and turns into buyer trap on bears
3. large gap down, fixed bullish plan is invalid until new structure proves otherwise

### 10.2 After a bearish day where sellers likely remain in control

If today opens:

1. flat or small gap down, default watch is sell-side continuation after weak bounce
2. mild gap up, watch for rejection and renewed sell-side pressure
3. large gap up, fixed bearish plan is invalid until new acceptance or failure appears

### 10.3 After a sideways or distribution day

If today opens:

1. flat, expect comfort farming unless a strong sweep changes it
2. small gap, watch whether the gap only harvests option sellers
3. strong move from open, classify later as trend, trap-then-trend, or gap-confusion day

### 10.4 Rule

Opening type decides who can be the target. Previous-day structure decides default bias. Stop availability decides whether trap or continuation is more likely.

## 11. Trading zones

### 11.1 Discount

Treat below or near value as discount when:

1. price is near previous day low, key support, or deep pullback value
2. put-side pain can increase quickly if reclaim appears

### 11.2 Inflated

Treat above or near value as inflated when:

1. price is near previous day high, key resistance, or extended upside
2. call-side pain can increase quickly if rejection appears

### 11.3 Fair

Fair zone is where:

1. both sides are comfortable
2. premiums decay
3. edge is weak

Avoid fresh entries from the fair zone unless a major breakout is accepted.

## 12. Session master levels

The engine must fix the important session levels early and stop redrawing them every minute.

### 12.1 Required master levels

1. previous day high
2. previous day low
3. previous day close
4. opening range high
5. opening range low
6. session high
7. session low
8. nearest major round numbers

### 12.2 Rule

Levels remain stable. Decisions change.

The system may update:

1. bias
2. acceptance or rejection status
3. hold or exit state
4. new-entry eligibility

But it should not keep inventing new master levels from every candle.

## 13. Acceptance vs rejection

### 13.1 Rejection

Treat a sweep as rejection when:

1. price quickly returns back through the level
2. follow-up candles confirm the reclaim
3. premium response supports the reversal

### 13.2 Acceptance

Treat a move as accepted when:

1. price holds beyond the breached level
2. pullbacks fail to reclaim the old zone
3. continuation remains orderly

### 13.3 Rule

Do not short where market is accepting above value. Do not buy where market is accepting below value.

## 14. Reclaim score

Reclaim quality must be scored instead of guessed.

Give one point for each:

1. close back inside the important level
2. reclaim happens within `3-7` candles of the sweep
3. displacement appears after reclaim
4. volume or participation expands on reclaim
5. structure shifts in favor of the reclaim direction

### 14.1 Interpretation

1. `0-1` = weak, avoid
2. `2` = possible but needs caution
3. `3` = tradable
4. `4-5` = A-grade reclaim

## 15. Trap classification

### 15.1 Bull trap

Bull trap exists when:

1. price breaks above important liquidity
2. breakout is weak, late, or comfort-driven
3. price re-enters the old range and acceptance fails

### 15.2 Bear trap

Bear trap exists when:

1. price breaks below important liquidity
2. breakdown is weak, late, or panic-driven
3. price reclaims the old range and selling fails

### 15.3 Support or resistance creation trap

Support or resistance is not always real. Sometimes it is only a temporary comfort zone used to invite fresh losers before the true move begins.

## 16. Day-type classification

Classify the live session into one main regime:

1. trend day
2. trap day
3. trap-then-trend day
4. double-side SL hunt day
5. slow grind day
6. sideways farming day
7. range-to-direction day
8. gap-confusion day

### 16.1 Trend day

Strong displacement, shallow pullbacks, and acceptance in one direction.

### 16.2 Trap day

Major sweep and immediate reclaim or rejection with reversal follow-through.

### 16.3 Trap-then-trend day

One side is harvested first, then the real directional move develops cleanly.

### 16.4 Double-side SL hunt day

Both sides get trapped at different times. Aggressive holding becomes dangerous.

### 16.5 Slow grind day

Price moves gradually without clean trap signatures. Avoid forcing reversal logic.

### 16.6 Sideways farming day

Wicks, decay, repeated failed breaks, and option-writer comfort dominate.

### 16.7 Range-to-direction day

Long balance phase eventually breaks with real acceptance.

### 16.8 Gap-confusion day

Opening gap destroys prior assumptions and the session needs reset logic before bias becomes tradable.

## 17. Market bias engine

The bias engine answers only:

1. which side is vulnerable
2. which side has the cleaner path
3. is the day more likely continuation, reversal, double-side trap, or decay
4. which master levels matter most right now

The bias engine does not place, trail, or exit trades. It only sets directional context.

## 18. Entry architecture

Level is not entry. Trigger is entry.

### 18.1 Aggressive entry

Allowed only when:

1. sweep occurs at Tier 1 liquidity
2. opening bias supports the direction
3. reclaim score is at least `3`
4. reward remains adequate

### 18.2 Confirmation entry

Preferred when:

1. reclaim or rejection is visible
2. retest holds
3. acceptance moves in favor of the intended trade

### 18.3 Retest rule

After break or reclaim:

1. if retest holds, quality improves
2. if retest immediately fails, avoid forcing entry

## 19. Long entry rules

Take bullish trades only when most of these align:

1. downside liquidity is swept or pressured
2. price reclaims a key level or holds above acceptance
3. previous-close or previous-low behavior supports the long
4. opening bias does not oppose the long
5. reclaim score is sufficient
6. fair-zone congestion is not the active location

## 20. Short entry rules

Take bearish trades only when most of these align:

1. upside liquidity is swept or pressured
2. price rejects a key level or accepts below it
3. previous-close or previous-high behavior supports the short
4. opening bias does not oppose the short
5. reclaim score or rejection quality is sufficient
6. fair-zone congestion is not the active location

## 21. Trade-management engine

The trade-management engine is separate from the bias engine.

It answers only:

1. hold
2. trail
3. partial
4. full exit
5. do nothing

### 21.1 Hold when

1. original bias remains valid
2. acceptance still favors the position
3. no opposite A-grade reclaim appears

### 21.2 Trail when

1. first objective is reached
2. price displaces further in favor
3. structure forms a new defended zone

### 21.3 Partial when

1. day type suggests double-side harvesting risk
2. important opposing liquidity is reached
3. trade has already paid enough to remove emotional pressure

### 21.4 Exit when

1. the defended level is lost
2. opposite acceptance appears
3. opposite A-grade reclaim appears
4. day-type read changes against the position

### 21.5 Optional fear-control ladder

If the operator prefers fixed discipline:

1. near `+10` points on spot logic, risk can reduce toward cost
2. near `+20` points, partial can be considered
3. the remainder can trail by structure

This ladder is optional and must be backtested per instrument and premium behavior.

## 22. Risk engine

### 22.1 Core capital rules

1. never use borrowed money
2. divide capital so one bad session does not break the month
3. risk must remain small enough to survive a losing streak

### 22.2 Loss rules

1. stop trading after daily damage limit is hit
2. monthly loss near `7-8%` is a serious warning zone
3. never double size after loss to recover emotionally

### 22.3 Quantity rules

1. size must come from risk, not confidence alone
2. lower-quality setups deserve smaller size or no trade

## 23. Attempt and overtrading control

1. set a maximum number of serious trade attempts per session
2. repeated failure around the same level usually means edge is weak or the day is farming both sides
3. fewer high-quality setups beat constant clicking

## 24. Fast-trading protocol

Fast trading does not mean random trading. It means predefined execution.

Before market:

1. define the vulnerable side
2. define master levels
3. define preferred setup types
4. define invalidation conditions

During market:

1. wait for the setup family you already planned
2. act fast only after conditions are met
3. avoid creating new logic from fear or excitement

## 25. Setup minimalism

The live engine should specialize in a small number of high-quality setups:

1. Tier 1 sweep and reclaim
2. acceptance breakout after real pressure
3. trap-then-trend continuation
4. previous-close reclaim or rejection trade

Anything outside these should be treated as secondary until proven valuable by data.

## 26. Confidence scoring

Start from neutral and add or subtract weight from:

1. opening bias alignment
2. liquidity tier importance
3. reclaim score
4. day-type clarity
5. writer pain or comfort evidence
6. acceptance or rejection quality
7. reward-to-risk quality

Do not assign high confidence just because one candle looks strong.

## 27. No-trade conditions

Do not trade when:

1. price sits inside fair-value comfort without pressure
2. signal appears only at Tier 3 liquidity
3. opening context is unresolved after a large gap
4. reclaim score is weak
5. day type is sideways farming and edge is unclear
6. move is already extended and requires chase

## 28. Option selection rule

For Nifty option entries in this system:

1. if spot is at an exact hundred like `24500`, buy the `24500` strike option
2. if spot is between strikes like `24498`, use the lower hundred call strike for CE such as `24400CE`
3. if spot is between strikes like `24498`, use the higher hundred put strike for PE such as `24500PE`
4. this remains an execution rule, not a directional rule

## 29. Session workflow

### Before market

1. classify previous day
2. map Tier 1 and Tier 2 liquidity
3. mark previous close importance
4. define likely opening bias paths
5. note risk limits and preferred setup families

### After open

1. classify opening type
2. update the vulnerable side
3. classify likely day type
4. wait for acceptance, rejection, or reclaim evidence

### During trade

1. keep bias engine and management engine separate
2. do not re-invent levels every minute
3. update hold, trail, partial, or exit status only from new evidence

### After market

1. review whether opening bias was correct
2. review whether day type was correctly labeled
3. review whether entries respected reclaim score and liquidity tier
4. record whether fear or overtrading damaged execution

## 30. Implementation modules

The app can convert this into modules:

1. `previous_day_classifier`
2. `opening_type_classifier`
3. `liquidity_tier_engine`
4. `writer_pain_engine`
5. `acceptance_rejection_engine`
6. `reclaim_score_engine`
7. `day_type_classifier`
8. `bias_engine`
9. `entry_engine`
10. `trade_management_engine`
11. `risk_engine`
12. `review_logger`

## 31. v5 assumptions to validate

The following still need historical testing and calibration:

1. exact reclaim window of `3-7` candles
2. strong-move threshold around `0.35%` in `15` minutes
3. large-gap threshold around `200-300` points
4. optional partial-booking ladder values
5. which day-type tags produce the best option premium outcomes

## 32. One-line system summary

Map important liquidity, read the opening through previous-day structure, decide whether the move is accepted or rejected, trade only high-quality sweeps or continuations, and manage the position separately from the bias.
