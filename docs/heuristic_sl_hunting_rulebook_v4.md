# Heuristic SL Hunting Rulebook v4

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

This v4 rulebook keeps the v3 trading logic and adds the next layer the newer documents kept reinforcing:

1. execution governance
2. gap-fill skepticism
3. timeframe hierarchy
4. capital and loss-control rules
5. fast-trading protocol
6. setup minimalism
7. learning and review process

This is now a more complete heuristic trading operating system, not only a pattern rulebook.

## 1. Core belief

Market moves toward liquidity, not toward our opinion.

The best intraday trade usually appears after:

1. obvious liquidity is mapped
2. the crowd becomes comfortable or trapped
3. the level is swept
4. price either reclaims or rejects that sweep
5. the live regime confirms whether this is a trap day, trend day, or grind day

## 2. Priority hierarchy

When signals conflict, follow this order:

1. psychology
2. liquidity
3. writer comfort and writer pain
4. opening context
5. live regime
6. structure
7. execution trigger

Structure frames the trade. Liquidity and psychology decide whether the trade should exist at all.

## 3. Market inputs

The engine should read:

1. previous day high, low, close
2. current day 1-minute candles from open
3. first 5-minute range
4. first 15-minute range
5. session high and low
6. round numbers in `00` and `50`
7. weekly option-chain strikes near spot
8. monthly option-chain strikes near spot
9. premium and premium change at top OI strikes
10. fair-value proxy such as VWAP or session POC
11. relative behavior of Nifty, Bank Nifty, and Sensex
12. gap size and opening location
13. expiry, Friday, or holiday context
14. current running-trade state if any
15. time-of-day bucket

## 4. Timeframe hierarchy

One of the main additions from the new documents is that timeframe itself does not create edge. Price movement quality does.

### 4.1 Timeframe usage

1. higher timeframe gives context
2. lower timeframe gives entry
3. breakout strength depends on displacement, not on whether the candle came from 1-minute or 5-minute chart

### 4.2 Practical use

1. use `15m-1h` for broad intraday bias
2. use `5m-15m` for structure context
3. use `1m` for sweep, reclaim, retest, and entry timing

### 4.3 Rule

Do not say:

1. “5-minute breakout is stronger just because it is 5-minute”

Instead ask:

1. how far did price move
2. how fast did it move
3. was the move accepted or rejected

## 5. Liquidity persistence

Price movement does not automatically mean traders exited.

### 5.1 Strong move vs weak move

1. strong move means the trapped side is more likely removed
2. weak move means the trapped side may still be holding and remains future fuel

### 5.2 Strong move definition

Treat move as strong if one or more are true:

1. three same-direction 1-minute candles expand with momentum
2. directional move exceeds about `0.35%` within `15` minutes
3. retracements are shallow and quickly absorbed

If these are absent, do not assume liquidation happened.

## 6. Writer comfort and pain

Spot crossing a strike slightly does not mean the writer is under stress.

### 6.1 Writer comfort

Writer remains comfortable when:

1. spot crossed the level only slightly
2. premium has not expanded materially
3. no panic or covering behavior is visible

### 6.2 Writer pain

Treat writer as under pressure only when:

1. spot pushes meaningfully through the strike
2. premium expands sharply
3. continuation keeps forcing the move

Until then, default to trap suspicion rather than trend certainty.

## 7. Gap logic

### 7.1 Gap is setup, not signal

1. gap up is not automatically short
2. gap down is not automatically long
3. opening behavior must validate the path

### 7.2 Gap-fill skepticism

A major new addition from the recent documents:

1. just because a gap exists does not mean the market wants to fill it
2. if the crowd is waiting for the gap to fill, the market may avoid it
3. a gap becomes useful only if the structure and trapped-side logic support it

### 7.3 Large gap reset

If opening gap exceeds about `0.6%`, trust previous intraday micro-levels less.

If opening gap exceeds about `1.0%`, assume fresh regime unless immediate reclaim proves otherwise.

### 7.4 Very large gap

If Nifty-scale opening gap exceeds about `300` points:

1. old micro-levels lose immediate importance
2. wait `15-30` minutes
3. mark fresh opening range
4. classify the new regime before trading

## 8. Opening psychology

### 8.1 Opening pullback advantage

When broader chart is positive and open is flat or mildly up:

1. direct breakout buy is risky
2. initial dip activates sellers
3. two-sided market gets created
4. recovery after the dip becomes better long material

### 8.2 One-sided open

If market opens one-sided:

1. trap risk rises
2. avoid chasing
3. wait for pullback, reclaim, or rejection

### 8.3 Opening validation rule

The open does not predict the whole day. It validates whether the pre-market map still survives.

## 9. Trading zones

### 9.1 Discount

Price is in discount when:

1. below fair value by more than `0.25%`
2. near previous day low or session low after fear expansion
3. a forced selloff already happened

Bias:

1. bullish trap reversal preferred

### 9.2 Inflated

Price is in inflated when:

1. above fair value by more than `0.25%`
2. near previous day high or session high after euphoric expansion
3. breakout chasing is visible

Bias:

1. bearish trap reversal preferred

### 9.3 Fair

Price is in fair when:

1. within `+/-0.25%` of fair value
2. away from extreme liquidity

Bias:

1. avoid option buying
2. wait for edge sweep or hold/reclaim event

## 10. Session master levels

Levels should not change randomly through the session.

### 10.1 Rule

1. define session master long zone
2. define session master short zone
3. define mid/no-trade zone
4. keep them stable unless structure truly changes

### 10.2 Levels vs decisions

Levels stay fixed more often than decisions.

The live update should answer only:

1. current bias
2. running trade: hold, trail, partial, exit
3. new entry: valid or none

## 11. Acceptance vs rejection

### 11.1 Rejection

A level behaves as rejection when:

1. price reaches or sweeps the level
2. continuation fails
3. wick or fast reversal appears
4. price closes back through the level

### 11.2 Acceptance

A level behaves as acceptance when:

1. price reaches the level
2. price holds there
3. no fast rejection appears
4. candles build base instead of reversing

### 11.3 Rule

1. do not short where market is accepting above
2. do not long where market is accepting below
3. trade rejection, not stubborn level assumptions

## 12. Trap classification

### 12.1 Bull trap

1. price sweeps obvious high
2. extension is small, typically `10-50` points
3. price fails and closes back below
4. nearby call writers remain comfortable

### 12.2 Bear trap

1. price sweeps obvious low
2. extension is small, typically `10-50` points
3. price fails and closes back above
4. nearby put writers remain comfortable

### 12.3 Support/resistance creation trap

In downtrend:

1. market creates support
2. attracts buyers
3. later breaks and continues down

In uptrend:

1. market creates resistance
2. attracts sellers
3. later breaks and continues up

## 13. Regime classification

### 13.1 Trap day

Characteristics:

1. obvious sweep
2. fast reverse
3. clear pain transfer
4. reclaim or rejection confirmation

### 13.2 Slow grind day

Characteristics:

1. slow movement
2. no extreme spike
3. repeated hovering near levels
4. no clean reversal trigger

Interpretation:

1. operator may be distributing or absorbing
2. reduce target expectations
3. reduce size

### 13.3 Sideways farming day

Characteristics:

1. both sides get probed repeatedly
2. market remains in fair or mid zone
3. signals appear frequently but clean follow-through is rare

Interpretation:

1. trade less
2. edge exists only at extremes
3. do not force trend logic

## 14. Entry architecture

### 14.1 Level is not entry

1. level = where event happens
2. entry = where event is confirmed

### 14.2 Aggressive entry

Allowed only when:

1. level is major
2. rejection wick is clear
3. continuation against the idea is weak
4. trader accepts lower win rate

### 14.3 Confirmation entry

Default mode.

Allowed only when:

1. sweep happened
2. reclaim or rejection happened
3. next candle or retest held

### 14.4 Retest rule

Preferred structure:

1. break
2. retest
3. hold
4. entry

Breakout chasing is discouraged.

## 15. No-chase rule

If move already ran `40-60` points without pause:

1. do not enter late
2. wait for retest
3. if no retest comes, accept no trade

## 16. Long entry rules

Enter long only when all are true:

1. sell-side liquidity pool was swept
2. price reclaimed or strongly held above the level
3. next candle did not negate the reclaim
4. at least `3` confidence factors are present

Additional filters:

1. do not long if level is being accepted downward
2. do not long after unretraced `40-60` point move
3. do not long if large-gap regime has not stabilized yet

## 17. Short entry rules

Enter short only when all are true:

1. buy-side liquidity pool was swept
2. price rejected or strongly held back below the level
3. next candle did not negate the rejection
4. at least `3` confidence factors are present

Additional filters:

1. do not short where market is accepting above
2. do not short after unretraced `40-60` point move if rejection has not formed
3. do not short solely because price looks high

## 18. Running-trade management

The engine must support active-trade updates, not only fresh entries.

### 18.1 Every live update must decide one of:

1. hold
2. trail stop
3. partial book
4. exit

### 18.2 Hold when

1. structure still supports the trade
2. defended level is still respected
3. opposite side is not yet fully hunted
4. no strong invalidation candle appeared

### 18.3 Trail when

1. trade reached about `1R`
2. continuation happened but momentum started slowing
3. operator exit behavior starts appearing

### 18.4 Exit when

1. price closes back through defended zone
2. strong opposite candle breaks the thesis
3. acceptance develops against the trade
4. live regime changed against the position

## 19. Risk engine

This is one of the biggest v4 upgrades.

### 19.1 Core capital rules

1. do not trade with borrowed money
2. do not use full capital in one go
3. divide capital into smaller working parts
4. increase size only gradually and ideally from profits

### 19.2 Loss rules

1. avoid large losses above all else
2. monthly loss should remain controlled, with v4 heuristic cap around `7-8%`
3. if drawdown becomes emotionally disruptive, reduce size immediately

### 19.3 Quantity rules

1. never double size after a loss
2. do not jump from small size to very large size suddenly
3. size should rise only after stable execution and profit-based growth

### 19.4 Segment discipline

1. do not jump between stocks, options, futures, or other segments just to recover losses
2. improve in the same segment first

## 20. Attempt and overtrading control

### 20.1 Max attempts

1. max `1-2` serious attempts per idea
2. if same setup failed, do not keep forcing repeated re-entry without fresh structure

### 20.2 Sideways-market rule

1. in sideways market, reduce trade count to `1-2` trades max
2. overtrading is the default failure mode in chop

### 20.3 Smart work rule

1. more chart work, fewer trades
2. more clarity, less execution noise
3. not every opportunity belongs to you

## 21. Fast-trading protocol

Speed must come from preparation, not from panic.

### 21.1 Fast trading means

1. pre-defined scenarios exist
2. risk is small enough to act decisively
3. trade count is limited
4. setup is already understood before it appears

### 21.2 Fast trading does not mean

1. random clicking
2. revenge trading
3. immediate re-entry after loss without new setup

### 21.3 Plan of execution

Before live market, the engine should already know:

1. if condition A appears -> buy
2. if condition B appears -> sell
3. if neither appears -> wait

## 22. Setup minimalism

Another major v4 upgrade:

1. too many setups reduce clarity
2. system should rely on a small number of repeatable patterns
3. quality beats quantity

Recommended live focus:

1. downside sweep + reclaim long
2. upside sweep + rejection short
3. break-retest-hold continuation
4. large-gap reset regime

Everything else should be treated as lower priority.

## 23. Confidence and fear balance

### 23.1 Confidence

Confidence comes from:

1. clear rules
2. controlled risk
3. known invalidation

### 23.2 Fear

Fear rises when:

1. losses become too large
2. trade size is too big
3. no clear exit plan exists

### 23.3 Rule

The system should prefer setups where confidence can come from rules, not from hope.

## 24. Learning engine

### 24.1 Learning process

Learning must include:

1. understanding
2. implementation
3. repetition

### 24.2 Post-trade review

Every loss should ask:

1. who was trapped
2. who was still holding
3. was the move accepted or rejected
4. did I trade too early, too late, or correctly
5. was this trap-day logic or grind-day logic

### 24.3 Improvement rule

Do not keep changing strategies after each loss.

Instead:

1. keep one system
2. identify root cause
3. improve that system

## 25. Confidence scoring

Score each setup from `0` to `10`.

Add `1` point for each:

1. major sweep happened
2. reclaim or rejection close happened
3. level showed rejection, not acceptance
4. writer comfort or writer pain supports the idea
5. discount or inflated zone advantage
6. multi-index confirmation
7. opening context supports the idea
8. regime type supports the idea
9. retest held
10. opposing side is clearly trapped

Interpretation:

1. `0-3`: no trade
2. `4-5`: reduced size
3. `6-7`: standard trade
4. `8-10`: strong setup

## 26. No-trade conditions

Do not trade when any apply:

1. price is in fair or mid zone without sweep
2. level is being accepted, not rejected
3. move already ran and you are late
4. huge gap created new regime but no new map exists yet
5. session is slow grind with no clear pain transfer
6. weekly and monthly conflict with chop
7. news, RBI, or event risk is near
8. old bias is being forced despite new opening evidence
9. emotional need to recover loss is driving the trade
10. trade exists only because you want daily income

## 27. Option selection rule

For Nifty option buying:

1. bullish trade -> nearest lower hundred CE
2. bearish trade -> nearest higher hundred PE

Examples:

1. Spot `24500` -> `24500 CE` or `24500 PE`
2. Spot `24498` -> `24400 CE`
3. Spot `24498` -> `24500 PE`

## 28. Session workflow

### Before market

1. mark previous day high, low, close
2. mark weekly and monthly OI clusters
3. estimate writer comfort and danger zones
4. define master long zone, short zone, and mid zone
5. create flat, gap-up, and gap-down scenarios
6. pre-commit max risk and max trade count

### After open

1. determine if old map still valid
2. identify live regime
3. wait for first real clue: sweep, reclaim, rejection, or acceptance
4. do not force previous plan if opening disproves it

### During trade

1. update only bias, running-trade state, and new-entry validity
2. keep levels fixed unless structure truly changes
3. manage around next liquidity pool
4. stop if session turns into mismatch day

### After market

1. review whether the thesis matched the live regime
2. note whether trapped traders had exited or were still holding
3. update journal before touching rulebook

## 29. Implementation modules

The next coding version should implement:

1. bias engine
2. liquidity-persistence engine
3. writer comfort and writer-pain engine
4. session regime classifier
5. acceptance-vs-rejection detector
6. sweep-and-reclaim detector
7. master level manager
8. running-trade state manager
9. risk-governance engine
10. post-trade review logger

## 30. v4 assumptions to validate

Still heuristic:

1. fair-zone width: `0.25%`
2. weak breakout threshold: `10-50` points
3. no-chase filter: `40-60` points
4. strong move threshold: about `0.35%` within `15` minutes
5. large-gap regime reset around `0.6%-1.0%`
6. very large gap special handling for `300+` point context moves
7. monthly drawdown guidance near `7-8%`

## 31. One-line system summary

Do not trade the level alone. Trade the live state around the level: whether the crowd is still holding, whether the level is being accepted or rejected, whether the move is trap-speed or grind-speed, whether the setup still respects risk, and whether the current session truly deserves your participation.
