# Heuristic SL Hunting Rulebook v3

Source basis:

1. `numberical SL hunting by Amol.txt`
2. `transaction SL enginners.txt`
3. `tracipt of mr amol for SL hunting.txt`
4. `chat SL 1.txt`
5. `chat SL 2.txt`
6. `chat_SL 3.txt`
7. `chat_sl 4.txt`

This v3 rulebook keeps the v2 foundation and adds the next important upgrades:

1. fixed session levels vs changing decisions
2. running-trade update logic
3. level acceptance vs level rejection
4. liquidity persistence: exited vs still holding
5. slow grind vs true trap-day distinction
6. session regime handling for very large gaps
7. precise retest and non-chase rules
8. execution-state discipline for live updates

This is the first version that is close to a real desk-style heuristic engine instead of only a concept document.

## 1. Core belief

Market moves when liquidity exists, both sides are active, and larger capital decides to force movement.

The engine must answer these before any trade:

1. who is trapped
2. who is still holding, not yet stopped
3. whether the level is being accepted or rejected
4. whether the move is fast enough to be a trap or slow enough to be distribution
5. whether the session still follows the original plan or has shifted to a new regime

## 2. Priority hierarchy

When conflict exists, follow this order:

1. psychology
2. liquidity
3. writer comfort and danger zone
4. opening context
5. structure
6. execution trigger

Structure helps frame bias, but psychology and liquidity decide whether a level matters right now.

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
10. POC or a substitute fair-value zone
11. relative behavior of Nifty, Bank Nifty, and Sensex
12. gap size and opening location
13. expiry, Friday, or holiday context
14. current running-trade state if any

## 4. Planning horizon

The heuristic system must think in 3 layers:

1. pre-market scenario
2. live session regime
3. running-trade update state

The 2-to-3 day path remains useful, but live intraday decisions should come from the current regime, not old bias alone.

## 5. Liquidity persistence

One of the most important v3 upgrades:

price movement does not automatically mean traders exited.

### 5.1 Strong move vs weak move

1. Strong move: traders more likely exited, SL already consumed.
2. Weak move: traders more likely still holding, SL remains as future fuel.

### 5.2 Strong move definition

Treat a move as strong if one or more are true:

1. three same-direction 1-minute candles expand with momentum
2. directional move exceeds about `0.35%` within `15` minutes
3. retracements are shallow and quickly absorbed

If these are absent, do not assume liquidation happened.

### 5.3 Rule

Before entering, ask:

1. has the crowd already been removed
2. or are they still holding and becoming future SL

If uncertain, reduce confidence.

## 6. Writer comfort and danger zone

A spot breach is not enough. The engine must ask if the nearby writer is truly under stress.

### 6.1 Writer comfort

Writer remains comfortable when:

1. spot crossed the level only slightly
2. premium has not expanded meaningfully
3. no short-covering or panic behavior appears

### 6.2 Writer danger

Treat the writer as stressed only when:

1. spot pushes deep enough beyond the strike
2. premium expands sharply
3. follow-through keeps pressure on the writer

Until then, expect fake breaks more than true continuation.

## 7. Trading zones

### 7.1 Discount

Price is in discount when:

1. below fair value or POC by more than `0.25%`
2. near previous day low or session low after fear expansion
3. a forced selloff already happened

Bias:

1. bullish trap reversal preferred

### 7.2 Inflated

Price is in inflated when:

1. above fair value or POC by more than `0.25%`
2. near previous day high or session high after euphoric expansion
3. breakout chasing is visible

Bias:

1. bearish trap reversal preferred

### 7.3 Fair

Price is in fair when:

1. within `+/-0.25%` of fair value
2. away from extreme liquidity

Bias:

1. avoid option buying
2. wait for edge sweep or hold/reclaim event

## 8. Fixed session levels

This is a major execution rule from the new transcripts.

### 8.1 Levels vs decisions

Levels should not change randomly every few candles.

Rule:

1. session master levels are fixed after the early structure is established
2. decisions may change
3. levels update only when structure truly changes

### 8.2 When levels may change

Only update session master levels if:

1. new session high meaningfully breaks prior extreme and holds
2. new session low meaningfully breaks prior extreme and holds
3. very large gap created a new regime
4. market moved from range regime into trend regime or vice versa

### 8.3 Live desk format

Each update should answer only:

1. current bias
2. running trade: hold, exit, trail
3. new entry: valid or none

This prevents random level drift.

## 9. Acceptance vs rejection

This is one of the cleanest v3 upgrades.

### 9.1 Rejection

A level behaves as rejection when:

1. price reaches the level or sweeps above/below it
2. continuation fails
3. wick or fast reverse candle appears
4. price closes back through the level

### 9.2 Acceptance

A level behaves as acceptance when:

1. price reaches the level
2. price holds above it or below it
3. there is no quick rejection
4. candles build base rather than reverse sharply

### 9.3 Rule

Do not short where market is accepting.
Do not long where market is accepting downward.
Trade rejection, not assumptions.

## 10. Gap and regime logic

### 10.1 Flat or small gap

If the market opens flat or with a small gap:

1. previous map still matters
2. first trap or first break matters
3. round numbers and opening range become important

### 10.2 Large gap

If opening gap exceeds about `0.6%`, trust old intraday micro-levels less.

If opening gap exceeds about `1.0%`, assume the market opened in a fresh regime unless immediate reclaim proves otherwise.

### 10.3 Very large gap

If gap exceeds about `300` points in Nifty-scale context:

1. do not use previous session levels as immediate execution levels
2. wait `15-30` minutes
3. mark new opening range high and low
4. decide whether the regime is:
   - strong trend
   - spike and rejection
   - sideways balance

Rule:

1. big gap means patience must increase
2. first move is often noise
3. second move may be trap
4. third move is more likely real

## 11. Opening psychology

### 11.1 Opening pullback advantage

When broader chart is positive and the market opens flat or mildly up:

1. direct breakout buy is risky
2. initial dip activates sellers
3. two-sided participation is created
4. recovery after that dip becomes better long material

### 11.2 One-sided open

If market opens and moves only one-sided:

1. trap risk rises
2. avoid chasing
3. wait for pullback, reclaim, or structure

### 11.3 Gap is setup, not signal

1. gap up is not automatically short
2. gap down is not automatically long
3. opening behavior must validate the path

## 12. Round-number rule

Round numbers are psychological liquidity magnets.

Examples:

1. `42000`
2. `54000`
3. `24300`
4. `25800`

Rule:

1. do not trade only because price touched a round number
2. require break-and-hold or sweep-and-reject behavior
3. round-number breach alone is not trend confirmation

## 13. Operator behavior

### 13.1 Sudden vs fragmented capital

1. retail flow is fragmented
2. smart money appears as sudden pressure or sudden defense

Track:

1. abnormal velocity
2. sudden expansion origin
3. whether it triggers chain reaction

### 13.2 Gradual build then sudden move

Typical operator sequence:

1. slow build
2. liquidity creation
3. sudden push

If the move is only slow with no sudden push, assume distribution or balancing first.

### 13.3 Operator exit

Assume exit or profit booking when:

1. big move already happened
2. candles become small and alternating
3. price stops extending
4. there is no fresh pressure despite open profit

## 14. Slow grind vs trap day

Another important v3 distinction:

### 14.1 Slow grind day

Characteristics:

1. slow movement
2. no extreme spike
3. repeated hovering near levels
4. no clean reversal trigger

Interpretation:

1. operator may be distributing or absorbing
2. not a clean SL-hunt day
3. expect scalp logic, not big expansion expectations

### 14.2 True trap day

Characteristics:

1. obvious sweep
2. fast reverse
3. visible pain transfer
4. reclaim or rejection confirmation

Interpretation:

1. high-quality SL-hunt logic is active

### 14.3 Rule

If session is slow grind:

1. reduce target expectations
2. reduce size
3. avoid forcing strong trend thesis

## 15. Trap classification

### 15.1 Bull trap

1. price sweeps obvious high
2. extension is small, typically `10-50` points
3. price fails and closes back below
4. call writers remain comfortable

Action:

1. bearish setup becomes valid after rejection confirmation

### 15.2 Bear trap

1. price sweeps obvious low
2. extension is small, typically `10-50` points
3. price fails and closes back above
4. put writers remain comfortable

Action:

1. bullish setup becomes valid after reclaim confirmation

### 15.3 Support/resistance creation trap

In downtrend:

1. market creates support
2. attracts buyers
3. later breaks and continues down

In uptrend:

1. market creates resistance
2. attracts sellers
3. later breaks and continues up

## 16. Entry architecture

### 16.1 Level is not entry

1. level = where event happens
2. entry = where event is confirmed

### 16.2 Aggressive entry

Allowed only when:

1. major level
2. clear wick rejection
3. no strong continuation against the idea
4. trader accepts lower win rate

### 16.3 Confirmation entry

Default mode.

Allowed only when:

1. sweep happened
2. reclaim or rejection happened
3. next candle or retest held

### 16.4 Retest rule

Preferred structure:

1. break
2. retest
3. hold
4. entry

Breakout candle chasing is discouraged.

## 17. No-chase rule

If move already ran `40-60` points without pause:

1. do not enter late
2. wait for retest
3. if no retest comes, accept no trade

You are not required to catch every move.

## 18. Long entry rules

Enter long only when all are true:

1. sell-side liquidity pool was swept
2. price reclaimed or strongly held above the level
3. next candle did not negate the reclaim
4. at least `3` confidence factors are present

Additional filters:

1. do not long if level is being accepted downward
2. do not long after unretraced `40-60` point move
3. do not long if large-gap regime has not stabilized yet

## 19. Short entry rules

Enter short only when all are true:

1. buy-side liquidity pool was swept
2. price rejected or strongly held back below the level
3. next candle did not negate the rejection
4. at least `3` confidence factors are present

Additional filters:

1. do not short where market is accepting above resistance
2. do not short after unretraced `40-60` point move if rejection has not formed
3. do not short solely because price “looks high”

## 20. Running-trade management

This is a major v3 addition.

The engine should support active-trade updates, not only fresh entries.

### 20.1 If a trade is already running

Every update must decide one of:

1. hold
2. trail stop
3. partial book
4. exit

### 20.2 Hold rules

Hold when:

1. structure still supports the trade
2. defended level is still respected
3. opposite side is not yet fully hunted
4. no strong invalidation candle appeared

### 20.3 Trail rules

Trail when:

1. trade reached about `1R`
2. market showed continuation but momentum started slowing
3. operator exit behavior starts appearing

### 20.4 Exit rules

Exit when:

1. price closes back through defended zone
2. strong opposite candle appears and breaks structure
3. acceptance develops against the trade
4. live regime changed from trap day to slow distribution against the position

## 21. Session master levels and mid zone

Every session should define:

1. one or two master long zones
2. one or two master short zones
3. one mid/no-trade zone

Rule:

1. most noise happens in the mid zone
2. edge is highest near liquidity edges, not in the middle

## 22. Multi-index confirmation

Use Nifty, Bank Nifty, and Sensex together.

1. if all three align, confidence increases
2. if two fall and one holds, the holding index can become reversal candidate
3. if one already moved big and another is still flat, do not blindly assume “now it must follow”

## 23. Profit booking vs real reversal

Do not treat every bounce as fresh buying.

A bounce may be:

1. short profit booking
2. holiday risk reduction
3. expiry-related decay and closing

Require:

1. reclaim
2. hold
3. follow-through

before upgrading a bounce into real reversal.

## 24. Holiday and expiry logic

### 24.1 Expiry

1. slow moves can be writer-dominated
2. next-day reversal risk rises
3. trap behavior becomes more important than simple momentum reading

### 24.2 Holiday or weekend

1. positions may close for non-directional reasons
2. support/resistance may reflect profit booking more than fresh conviction

Reduce confidence in apparent reversal right before long breaks.

## 25. Risk and attempt control

1. no averaging
2. max `1-2` serious attempts per idea
3. if session repeatedly mismatches the model, stop trading
4. reduce size in transition or distribution regimes

## 26. Confidence scoring

Score each setup from `0` to `10`.

Add `1` point for each:

1. major sweep happened
2. reclaim or rejection close happened
3. level showed rejection, not acceptance
4. writer comfort supports the idea
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

## 27. No-trade conditions

Do not trade when any apply:

1. price is in fair or mid zone without sweep
2. level is being accepted, not rejected
3. move already ran and you are late
4. huge gap created new regime but no new map exists yet
5. session is slow grind with no clear pain transfer
6. weekly and monthly conflict with chop
7. news, RBI, or event risk is near
8. old bias is being forced despite new opening evidence

## 28. Option selection rule

For Nifty option buying:

1. bullish trade -> nearest lower hundred CE
2. bearish trade -> nearest higher hundred PE

Examples:

1. Spot `24500` -> `24500 CE` or `24500 PE`
2. Spot `24498` -> `24400 CE`
3. Spot `24498` -> `24500 PE`

## 29. Session workflow

### Before market

1. mark previous day high, low, close
2. mark weekly and monthly OI clusters
3. estimate writer comfort and danger zones
4. define master long zone, short zone, and mid zone
5. create flat, gap-up, and gap-down scenarios

### After open

1. determine if old map still valid
2. identify session regime
3. wait for first real clue: sweep, reclaim, rejection, or acceptance
4. do not force previous plan if opening disproves it

### During trade

1. update only bias, running-trade state, and new-entry validity
2. levels stay fixed unless structure truly changes
3. manage around next liquidity pool

## 30. Implementation modules

The next coding version should implement:

1. bias engine
2. liquidity-persistence engine
3. writer comfort and danger-zone engine
4. session regime classifier
5. acceptance-vs-rejection detector
6. sweep-and-reclaim detector
7. master level manager
8. running-trade state manager
9. confidence scoring and risk manager

## 31. v3 assumptions to validate

Still heuristic:

1. fair-zone width: `0.25%`
2. weak breakout threshold: `10-50` points
3. no-chase filter: `40-60` points
4. strong move threshold: about `0.35%` within `15` minutes
5. large gap regime reset around `0.6%-1.0%`
6. very large gap special handling for `300+` point context moves

## 32. One-line system summary

Do not trade the level alone. Trade the live state around the level: whether the crowd is still holding, whether the level is being accepted or rejected, whether the move is trap-speed or distribution-speed, and whether the running trade still respects the original pain thesis.
