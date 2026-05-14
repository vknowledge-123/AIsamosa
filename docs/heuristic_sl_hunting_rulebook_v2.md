# Heuristic SL Hunting Rulebook v2

Source basis:

1. `numberical SL hunting by Amol.txt`
2. `transaction SL enginners.txt`
3. `tracipt of mr amol for SL hunting.txt`

This v2 rulebook improves the first deterministic version by adding:

1. opening-scenario logic
2. 2-to-3 day planning logic
3. holiday and expiry psychology
4. seller comfort and danger-zone logic from option premium
5. confirmation-entry rules vs aggressive level entries
6. profit-booking vs true reversal distinction
7. stricter no-trade filters

It is still a heuristic system, but it is much closer to a codable trading framework.

## 1. Core belief

Market moves to locate liquidity, remove weak hands, and transfer pain from one crowd to another.

The engine should always ask:

1. Who is trapped right now?
2. Who is still comfortable and not under pressure?
3. Has one side already been removed by a strong move?
4. Is this move attracting fresh traders or removing old traders?
5. Is this a level event, a reclaim event, or a continuation event?

## 2. Required market inputs

The engine should read:

1. Previous day high, low, and close
2. Current day 1-minute candles from open
3. Session high and low
4. First 5-minute and first 15-minute range
5. Round-number levels in 50 and 100 point increments
6. Weekly option-chain strikes within 5 to 7 strikes above and below spot
7. Monthly option-chain strikes within 5 to 7 strikes above and below spot
8. Premium and change in premium for the top nearby OI strikes
9. Volume profile POC if available
10. Relative strength of Nifty, Bank Nifty, and Sensex
11. Gap size and opening location vs previous close
12. Expiry-day or holiday/weekend context

If option-chain or volume-profile data is missing, the engine may still trade, but confidence must be reduced.

## 3. Planning horizon

The rulebook should not think candle-to-candle only. It should maintain:

1. immediate intraday bias
2. next-session bias
3. 2-to-3 day path expectation

The 2-to-3 day path asks:

1. If chart is bullish, how will price rise: gap up, dip first, or sideways trap first?
2. If chart is bearish, how will price fall: gap down, relief bounce first, or range trap first?
3. Has the market already delivered a big move that is unlikely to repeat in the same clean way the next day?

## 4. Market bias framework

### 4.1 Expiry priority

1. If monthly expiry is within 5 trading sessions, monthly structure has priority.
2. If monthly expiry is more than 5 trading sessions away, weekly structure has priority.
3. If weekly and monthly align, confidence increases.
4. If weekly and monthly conflict, prefer trap and range setups over straight continuation.

### 4.2 Writer interpretation

1. High call OI above price suggests resistance.
2. High put OI below price suggests support.
3. Strong call writing above plus strong put writing below suggests range.
4. Breakout is weak if price does not materially damage the nearest strong writer.

### 4.3 Strong writer definition

A writer zone is strong when all are true:

1. It is among the top 2 OI clusters nearby.
2. OI is stable or increasing.
3. Premium is not rapidly exploding against the writer.
4. Price tested the zone at least twice.

## 5. Seller comfort and danger zone

The new documents add an important refinement: a level is not broken just because spot crossed it slightly. A seller only feels true pressure after the premium danger zone is reached.

### 5.1 Premium danger-zone estimate

For a major writer strike:

1. estimate credit buffer from current premium plus recent premium change
2. compute approximate danger zone beyond the strike
3. until price approaches that danger zone, assume the writer can still be comfortable

Heuristic example:

1. Put writer at `46000`
2. Premium buffer around `276`
3. Spot move from `46000` to `45760` may still be a liquidity sweep, not a true support failure

### 5.2 Consequence

1. Do not trust a level break just because spot crossed it by `20-50` points.
2. If the writer is still comfortable, expect fake breakouts and fake breakdowns.
3. Confidence in trend continuation increases only when price pushes into the writer danger zone and premium stress becomes obvious.

## 6. Trading zones

### 6.1 Discount zone

Price is in discount when one or more are true:

1. price trades below session POC by more than `0.25%`
2. price is near previous day low or session low after a selloff
3. a fear move already happened and traders are hesitant to buy

Bias:

1. prefer bullish trap reversal
2. avoid fresh shorts unless downside continuation is very strong

### 6.2 Inflated zone

Price is in inflated when one or more are true:

1. price trades above session POC by more than `0.25%`
2. price is near previous day high or session high after a rally
3. euphoria or breakout chasing is visible

Bias:

1. prefer bearish trap reversal
2. allow bullish continuation only if breakout pressure is real

### 6.3 Fair zone

Price is in fair zone when it remains within `+/-0.25%` of POC and is not at an extreme.

Bias:

1. avoid fresh option buying
2. wait for edge sweep or clear reclaim
3. if trading at all, fade edges rather than chase mid-zone

## 7. Liquidity map

Mark these as liquidity pools:

1. previous day high
2. previous day low
3. current day high
4. current day low
5. equal highs within `10` points
6. equal lows within `10` points
7. round numbers ending in `00` and `50`
8. first 5-minute high and low
9. first 15-minute high and low
10. highest call OI strike nearby
11. highest put OI strike nearby
12. all-time high or recent major swing high if nearby

## 8. Operator-zone heuristic

An operator zone is assumed where a sharp reversal or sharp continuation begins.

### 8.1 Operator supply

Mark operator supply when all are true:

1. price approaches resistance or buy-side liquidity
2. one strong bearish candle or two sequential bearish candles appear
3. the move starts from a crowded breakout area or major high
4. follow-through lasts at least `2` candles or about `20` points

### 8.2 Operator demand

Mark operator demand when all are true:

1. price approaches support or sell-side liquidity
2. one strong bullish candle or two sequential bullish candles appear
3. the move starts from a crowded breakdown area or major low
4. follow-through lasts at least `2` candles or about `20` points

### 8.3 Operator exit

Assume the operator may be exiting when all are true:

1. a major breakout or breakdown already happened
2. candle bodies become small
3. price becomes choppy with alternating candles
4. price stops expanding despite apparent directional advantage

Interpretation:

1. tighten stops on continuation trades
2. do not force new continuation entries
3. prepare for reversal or mean reversion

## 9. Gap and opening logic

This is one of the biggest v2 upgrades.

### 9.1 Small gap and flat open

If the market opens flat or with a small gap:

1. keep previous structure relevant
2. wait for the market to reveal whether it wants to create a trap first
3. round numbers and first-break moves matter more

### 9.2 Large gap open

If the gap is large enough that a big crowd is already trapped or already in profit:

1. old intraday chart assumptions weaken
2. previous setup may become invalid
3. treat the opening as a new psychology regime

Heuristic v2:

1. if opening gap exceeds `0.6%`, reduce trust in previous intraday micro-levels
2. if opening gap exceeds `1.0%`, assume the day starts with a fresh map unless immediate reclaim proves otherwise

### 9.3 Gap down logic

1. Gap down plus immediate continued selling means sellers still active.
2. Gap down plus fast reclaim from discount zone suggests seller trap.
3. Gap down after a long decline can mean profit booking has already happened; avoid blindly chasing new shorts.

### 9.4 Gap up logic

1. Gap up into resistance or inflated zone with weak follow-through suggests a bull trap.
2. Gap up after a big prior up move can be dangerous for late buyers because the move may first hunt their stops.
3. Gap up with strong continuation is valid only if price keeps holding above reclaimed levels.

### 9.5 First-break rule

Do not trade the open just because candles are green or red.

Wait for one of these:

1. break and hold of a round number
2. sweep and reclaim of an opening extreme
3. rejection after a false opening breakout

## 10. Round-number rule

Round numbers are psychological levels and liquidity magnets.

Examples:

1. `42000`
2. `54000`
3. `25800`

Interpretation:

1. retail anchors around round numbers
2. stops cluster just above or below them
3. a small breach alone is not enough to validate a trend

The engine should require:

1. a close beyond the round number
2. then hold or reclaim behavior
3. then confirmation entry

## 11. Trap classification

### 11.1 Bull trap / fake breakout

A breakout is fake when all are true:

1. price sweeps an obvious high or resistance
2. extension is small, usually `10-50` points in v2
3. price closes back below the level within `1-3` candles
4. nearby call writers remain comfortable

Trade idea:

1. buy PE on rejection confirmation

### 11.2 Bear trap / fake breakdown

A breakdown is fake when all are true:

1. price sweeps an obvious low or support
2. extension is small, usually `10-50` points in v2
3. price closes back above the level within `1-3` candles
4. nearby put writers remain comfortable

Trade idea:

1. buy CE on reclaim confirmation

### 11.3 Clean trend continuation

A continuation is valid only when:

1. price broke a level
2. that level then held on retest or did not get retraced deeply
3. the opposing side is now trapped
4. the nearest writer is under real pressure, not just spot noise

## 12. Profit-booking vs new reversal

The new documents strongly stress this distinction.

Do not assume support means fresh buying.

A bounce may simply be:

1. profit booking by shorts
2. holiday risk reduction
3. expiry-related position closing

Heuristic:

1. after a large down move before a holiday or weekend, support is less trustworthy as a true bullish signal
2. after a large up move before a holiday or weekend, resistance is less trustworthy as a true bearish signal
3. require reclaim plus follow-through before treating profit-booking bounce as trend reversal

## 13. Holiday and expiry logic

### 13.1 Expiry day

1. options can expire worthless without manual profit booking
2. trend can look strong into expiry but reverse sharply next session
3. if the move is slow and option-writing structure dominates, expect trap behavior more than clean momentum

### 13.2 Friday and holiday effect

1. traders avoid carrying risk over holidays
2. profit booking can distort chart meaning
3. operators may build positions when retail participation is weak

Rule:

1. reduce confidence in apparent support or resistance that appears right before a long weekend
2. prefer continuation only after a fresh post-holiday confirmation

## 14. Multi-index confirmation

Use Nifty, Bank Nifty, and Sensex together.

### 14.1 Alignment

1. if all three align, confidence increases
2. if two fall and one holds, the holding index may become relative-strength candidate
3. if one index is unclear, use sibling indices to refine bias

### 14.2 Divergence

1. if Nifty is weak but Bank Nifty is holding strong demand, avoid aggressive shorts
2. if Nifty is strong but Bank Nifty is capped by strong call-writing, expect range or conflict behavior
3. if one index already moved big and the other is still flat, be careful assuming the second index must “follow now”

## 15. Setup library

### Setup A: Sell-side sweep reclaim long

Conditions:

1. price sweeps previous day low, session low, equal lows, or strong put-writer support
2. extension is small or stalls quickly
3. price closes back above the swept level
4. next `1-2` candles do not negate the reclaim
5. price is in discount or lower fair zone

Action:

1. buy CE

### Setup B: Buy-side sweep rejection short

Conditions:

1. price sweeps previous day high, session high, equal highs, or strong call-writer resistance
2. extension is small or stalls quickly
3. price closes back below the swept level
4. next `1-2` candles do not negate the rejection
5. price is in inflated or upper fair zone

Action:

1. buy PE

### Setup C: Range edge fade

Conditions:

1. strong writers exist on both sides
2. weekly and monthly do not align cleanly
3. price remains inside a known range
4. a sweep occurs at the edge without real writer stress

Action:

1. fade upper sweep with PE
2. fade lower sweep with CE

### Setup D: Opposite-side removal continuation

Conditions:

1. one side was already removed by a strong move
2. retracement is weak
3. fresh opposite traders enter
4. their stop-loss zone is obvious

Action:

1. trade in the direction of the next likely SL hunt

### Setup E: Operator-exit reversal

Conditions:

1. major breakout or breakdown already happened
2. momentum fades
3. small alternating candles appear
4. broken level starts acting opposite

Action:

1. trade reversal only after confirmation

### Setup F: Opening dip for bullish chart

Conditions:

1. broader chart is positive
2. market opens flat or mildly weak
3. opening dip creates fear and activates some sellers
4. price reclaims the opening weakness zone

Action:

1. buy only after reclaim and hold

### Setup G: All-time-high trap short

Conditions:

1. price is near all-time high or major recent high
2. crowd is overly bullish
3. gap-up continuation is expected by retail
4. market fails to continue cleanly

Action:

1. avoid blind longs
2. short only after clear rejection or failed hold

## 16. Entry architecture: aggressive vs confirmation

This is another major v2 addition.

### 16.1 Level is not entry

The document explicitly supports:

1. level = where trap event happens
2. entry = where trap is confirmed

### 16.2 Aggressive entry

Allowed only when:

1. the sweep level is major
2. rejection wick is strong
3. continuation against the level is weak
4. trader accepts lower win rate for better RR

### 16.3 Confirmation entry

Preferred default entry.

Allowed only when:

1. price first sweeps the level
2. then reclaims or rejects it
3. then holds the reclaim/rejection on the next candle or retest

Interpretation:

1. aggressive entry gives better price but lower confirmation
2. confirmation entry gives worse price but higher probability

## 17. Long entry rules

Enter long only when all are true:

1. a sell-side liquidity pool is swept
2. price closes back above it or strongly reclaims nearby structure
3. next candle does not fully negate the reclaim
4. at least `3` confidence factors are present

Extra v2 filter:

1. if the move from the low already ran `40-60` points without pause, do not chase; wait for retest

## 18. Short entry rules

Enter short only when all are true:

1. a buy-side liquidity pool is swept
2. price closes back below it or strongly rejects nearby structure
3. next candle does not fully negate the rejection
4. at least `3` confidence factors are present

Extra v2 filter:

1. if the move from the high already ran `40-60` points without pause, do not chase; wait for retest

## 19. Retest rule

Breakout candle entry is discouraged.

Preferred structure:

1. break
2. retest
3. hold
4. entry

If retest fails:

1. cancel continuation thesis
2. consider trap setup instead

## 20. Stop-loss rules

### Long stop

Place stop below the sweep low by the larger of:

1. `10` points
2. `0.08%` of spot

### Short stop

Place stop above the sweep high by the larger of:

1. `10` points
2. `0.08%` of spot

### Hard invalidation

Exit immediately if:

1. price closes back through defended zone
2. follow-through disappears within `2` candles
3. sibling indices turn strongly against the trade
4. writer comfort unexpectedly returns against the trade

## 21. Target and handling

### Initial target

Use nearest opposing liquidity as target 1:

1. for longs: nearest buy-side liquidity above
2. for shorts: nearest sell-side liquidity below

### Intraday handling rule

The new documents emphasize not overestimating intraday targets.

Rule:

1. intraday target should stay practical relative to stop
2. if momentum fades after `1R` or near target 1, protect capital first
3. large targets belong more naturally to positional logic than intraday option buying

### Management

1. at `+1R`, move stop to entry if momentum stalls
2. after strong continuation, trail below the latest bullish pivot for longs or above the latest bearish pivot for shorts
3. if operator-exit behavior appears, reduce thesis strength
4. if next liquidity pool shifts further due to expansion, target may be extended once

## 22. Confidence scoring

Score each setup from `0` to `8`.

Add `1` point for each:

1. sweep of major liquidity
2. reclaim or rejection close
3. weekly and monthly alignment
4. writer support for the thesis
5. discount or inflated zone advantage
6. multi-index confirmation
7. opening context supports the trade
8. danger-zone pressure supports the move

Interpretation:

1. `0-2`: no trade
2. `3-4`: low confidence, reduced size
3. `5-6`: standard trade
4. `7-8`: strong setup

## 23. No-trade conditions

Do not trade when any apply:

1. price is in fair zone with no sweep
2. breakout or breakdown is tiny and immediately dies
3. market already moved strongly and you are late
4. huge gap made prior intraday structure irrelevant but no new map is formed
5. weekly and monthly conflict and chart is choppy
6. news, RBI, policy, or major event is too close
7. profit-booking bounce is being mistaken for real reversal
8. only one side of logic is visible and opposite-side fuel is unclear

## 24. Option selection rule

For Nifty option buying:

1. bullish trade -> buy nearest lower hundred CE
2. bearish trade -> buy nearest higher hundred PE

Examples:

1. Spot `24500` -> `24500 CE` or `24500 PE`
2. Spot `24498` -> `24400 CE`
3. Spot `24498` -> `24500 PE`

## 25. Session workflow

### Before market

1. mark previous day high, low, close
2. mark weekly and monthly OI clusters
3. estimate writer comfort and danger zones
4. classify broad bias: bullish, bearish, range, or conflict
5. identify discount, fair, and inflated areas
6. create `flat open`, `gap up`, and `gap down` scenarios

### After open

1. observe whether opening confirms or invalidates the pre-market map
2. do not force the original plan if price behavior disagrees
3. wait for first real clue: sweep, reclaim, rejection, or break-and-hold
4. use sibling indices for confirmation
5. choose confirmation entry by default

### During trade

1. manage around next liquidity pool
2. monitor whether trapped-side logic is still valid
3. watch for operator exit
4. exit if thesis breaks, not because of random candle fear

## 26. Implementation modules

The next coding version should implement:

1. bias engine
2. 2-to-3 day scenario engine
3. zone engine
4. liquidity-map engine
5. writer comfort and danger-zone engine
6. sweep-and-reclaim detector
7. opening-context engine
8. multi-index confirmation engine
9. trade-scoring and trade-management engine

## 27. v2 assumptions to validate

These are still heuristic and should be backtested:

1. fair-zone width: `0.25%` around POC
2. weak breakout threshold: `10-50` points
3. strong move threshold: about `0.35%` within `15` minutes
4. chase filter: avoid entry after `40-60` point unretraced move
5. stop buffer: max of `10` points or `0.08%`
6. large-gap reset threshold: `0.6%-1.0%`

## 28. One-line system summary

Do not trade the obvious level itself. Trade the psychology around that level: who got trapped, who is still comfortable, whether the sweep was reclaimed, and whether the move has real pressure behind it.
