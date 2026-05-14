# Heuristic SL Hunting Rulebook v1

Source basis: `numberical SL hunting by Amol.txt`

This is the first deterministic version of the SL-hunting rulebook. It converts the document's concepts into rules that can later be coded, backtested, and refined. Where the source material was conceptual rather than numeric, this version uses explicit v1 thresholds so the strategy is testable.

## 1. Core belief

Market does not move only because of candles. It moves to:

1. take liquidity
2. trigger clustered stop losses
3. trap one side
4. move toward the side of maximum pain

The strategy should therefore ask these questions before every trade:

1. Who is trapped right now: buyers or sellers?
2. Where are obvious stop-loss clusters?
3. Has one side already been removed by a strong move?
4. Is price trading in discount, fair, or inflated zone?
5. Is current move real continuation or just SL hunting?

## 2. Required market inputs

The heuristic engine should read these inputs before making a decision:

1. Previous day high, low, and close
2. Current day 1-minute candles from market open
3. Session high and low
4. Nearby round-number levels in 50 and 100 point increments
5. Weekly option-chain strikes within 5 to 7 strikes above and below spot
6. Monthly option-chain strikes within 5 to 7 strikes above and below spot
7. Volume profile point of control for the current session, if available
8. Relative position of Nifty, Bank Nifty, and Sensex

If option-chain or volume-profile data is missing, the engine may still trade, but setup confidence must be reduced by one band.

## 3. Market bias framework

### 3.1 Expiry priority

1. If monthly expiry is within 5 trading sessions, monthly structure has priority.
2. If monthly expiry is more than 5 trading sessions away, weekly structure has priority.
3. If both weekly and monthly align, confidence increases.
4. If weekly and monthly conflict, prefer range or trap setups over breakout continuation.

### 3.2 Writer interpretation

1. High call OI above price suggests resistance.
2. High put OI below price suggests support.
3. Strong call writing above plus strong put writing below suggests range.
4. A breakout that does not materially pressure the nearest strong writer is considered weak.

### 3.3 Strong writer definition (v1)

A writer zone is strong when all are true:

1. It is among the top 2 OI clusters in the 5 to 7 nearby strikes.
2. OI is increasing versus the previous reading.
3. Premium remains comfortable for the writer and is not rapidly expanding against them.
4. Price has tested that strike or nearby chart level at least twice.

## 4. Trading zones

### 4.1 Discount zone

Price is in discount when at least one is true:

1. Price trades below session POC by more than 0.25 percent.
2. Price is near previous day low or current session low after a selloff.
3. Price has expanded sharply down and sentiment is fearful.

Bias:

1. Prefer bullish trap reversals
2. Avoid fresh shorts unless downside continuation is very strong

### 4.2 Inflated zone

Price is in inflated zone when at least one is true:

1. Price trades above session POC by more than 0.25 percent.
2. Price is near previous day high or current session high after a rally.
3. Price has expanded sharply up and sentiment is euphoric.

Bias:

1. Prefer bearish trap reversals
2. Allow bullish continuation only if breakout is strong and writers are under pressure

### 4.3 Fair zone

Price is in fair zone when it remains within plus or minus 0.25 percent of session POC and is not at an extreme.

Bias:

1. Avoid fresh option buying unless a clean sweep-and-reclaim setup appears
2. Prefer waiting or fading edges

## 5. Liquidity map

The engine should mark these as liquidity pools:

1. Previous day high
2. Previous day low
3. Current day high
4. Current day low
5. Equal highs within 10 points
6. Equal lows within 10 points
7. Round numbers ending in `00` and `50`
8. Highest OI call strike nearby
9. Highest OI put strike nearby
10. First 15-minute range high and low

## 6. Operator zone heuristic

An operator zone is assumed where a sharp reversal begins. Since operator activity cannot be seen directly, the engine approximates it from price behavior.

### 6.1 Operator supply zone

Mark operator supply when all are true:

1. Price approaches resistance or a buy-side liquidity pool
2. A strong down candle or a sequence of 2 bearish candles appears
3. The rejection starts from a recent swing high or crowded breakout area
4. Follow-through continues for at least 2 more candles or 20 points

### 6.2 Operator demand zone

Mark operator demand when all are true:

1. Price approaches support or a sell-side liquidity pool
2. A strong up candle or a sequence of 2 bullish candles appears
3. The reversal starts from a recent swing low or crowded breakdown area
4. Follow-through continues for at least 2 more candles or 20 points

### 6.3 Operator exit heuristic

Assume the operator may be exiting when all are true:

1. A major breakout or breakdown already happened
2. Candle bodies become small
3. Alternating candles appear
4. Price stops expanding despite being near the broken level

When operator exit is detected:

1. tighten stops on continuation trades
2. prepare for reversal or mean reversion

## 7. Trap classification

### 7.1 Bull trap / fake breakout

A bullish breakout is considered fake when all are true:

1. Price sweeps an obvious high or resistance zone
2. Extension above the level is small, usually 10 to 50 points for Nifty in v1
3. Price closes back below the broken level within 1 to 3 candles
4. Nearby call writers remain strong or are not materially pressured

Trade idea:

1. Buy put on rejection confirmation

### 7.2 Bear trap / fake breakdown

A bearish breakdown is considered fake when all are true:

1. Price sweeps an obvious low or support zone
2. Extension below the level is small, usually 10 to 50 points for Nifty in v1
3. Price closes back above the broken level within 1 to 3 candles
4. Nearby put writers remain strong or are not materially pressured

Trade idea:

1. Buy call on reclaim confirmation

### 7.3 Continuation after one-side removal

If a strong move has already removed one side, expect the next move to hunt the opposite side.

Define strong move in v1 as one of:

1. 3 same-direction 1-minute candles with expanding range
2. 0.35 percent directional move within 15 minutes
3. Clear breakout or breakdown followed by no meaningful retracement

Interpretation:

1. If buyers were removed first, look for seller trap and then upside
2. If sellers were removed first, look for buyer trap and then downside

## 8. Gap psychology

### 8.1 Gap down

1. Gap down plus immediate continued selling means sellers still active: prefer sell continuation.
2. Gap down plus fast reclaim from discount zone means sellers may be trapped: prefer bullish reversal.

### 8.2 Gap up

1. Gap up into resistance or inflated zone with weak follow-through means possible bull trap: prefer sell reversal.
2. Gap up plus strong continuation and writer pressure means bullish continuation is allowed.

### 8.3 Large gap caution

Avoid fresh entry in the first 5 minutes when:

1. gap exceeds 0.6 percent
2. news or event risk is present

## 9. Multi-index confirmation

Use Nifty, Bank Nifty, and Sensex together.

### 9.1 Alignment rule

1. If all three align directionally, confidence increases.
2. If two fall and the third holds, the third may become relative-strength candidate.
3. If Nifty is unclear, check Bank Nifty and Sensex before taking a trade.

### 9.2 Divergence use

1. If Nifty is sweeping lows but Bank Nifty is holding demand, avoid aggressive shorts.
2. If Nifty is sweeping highs but Bank Nifty is weak, avoid aggressive longs.

## 10. Setup library

### Setup A: Sell-side sweep reclaim long

Conditions:

1. Price sweeps previous day low, session low, equal lows, or strong put-writer support
2. Breakdown extension is small or stalls quickly
3. Price closes back above the swept level
4. A follow-through bullish candle confirms within the next 2 candles
5. Price is in discount or lower fair zone

Action:

1. Buy CE

### Setup B: Buy-side sweep rejection short

Conditions:

1. Price sweeps previous day high, session high, equal highs, or strong call-writer resistance
2. Breakout extension is small or stalls quickly
3. Price closes back below the swept level
4. A follow-through bearish candle confirms within the next 2 candles
5. Price is in inflated or upper fair zone

Action:

1. Buy PE

### Setup C: Range-bound edge fade

Conditions:

1. Strong writers exist on both sides
2. Monthly and weekly do not align cleanly
3. Price remains inside a known range
4. An edge sweep occurs without strong follow-through

Action:

1. Fade upper sweep with PE
2. Fade lower sweep with CE

### Setup D: Continuation after opposite side removal

Conditions:

1. A strong move already removed one side
2. Price retraces weakly
3. Fresh traders enter against the larger move
4. Their stop-loss zone is obvious

Action:

1. Enter in direction of the next likely SL hunt

### Setup E: Operator exit reversal

Conditions:

1. Breakdown or breakout has already happened
2. Momentum fades near extension extreme
3. Small alternating candles appear
4. The market fails to continue despite apparent advantage

Action:

1. Trade reversal only after reclaim or rejection confirmation

## 11. Entry rules

The heuristic engine should not enter on the first touch alone.

### Long entry

Enter long only when all are true:

1. A sell-side liquidity pool is swept
2. Price closes back above that level
3. The next candle does not fully negate the reclaim
4. At least 3 of the scoring factors in section 14 are present

### Short entry

Enter short only when all are true:

1. A buy-side liquidity pool is swept
2. Price closes back below that level
3. The next candle does not fully negate the rejection
4. At least 3 of the scoring factors in section 14 are present

## 12. Stop-loss rules

### Long stop

Place stop below the sweep low by the larger of:

1. 10 points
2. 0.08 percent of spot

### Short stop

Place stop above the sweep high by the larger of:

1. 10 points
2. 0.08 percent of spot

### Early exit

Exit immediately if:

1. price closes back through the defended zone
2. follow-through disappears within 2 candles
3. opposite index confirmation becomes strongly adverse

## 13. Target and management rules

### Initial target

Use the nearest opposing liquidity pool as target 1:

1. for longs: nearest buy-side liquidity above
2. for shorts: nearest sell-side liquidity below

### Risk-reward filter

Do not take a trade if target 1 offers less than 1.5R.

### Trade management

1. At +1R, move stop to entry if momentum stalls.
2. After a strong continuation candle, trail stop below the most recent bullish low for longs or above the most recent bearish high for shorts.
3. If operator-exit behavior appears, reduce position thesis strength and prepare to exit.
4. If the next opposing liquidity pool shifts further away because of expansion, target may be extended once.

## 14. Confidence scoring

Score each setup from 0 to 6.

Add 1 point for each:

1. sweep of major liquidity level
2. reclaim or rejection close
3. weekly and monthly alignment
4. option-chain writer support for the thesis
5. discount or inflated zone advantage
6. multi-index confirmation

Interpretation:

1. 0 to 2 points: no trade
2. 3 points: low-confidence trade, reduced size
3. 4 points: standard trade
4. 5 to 6 points: strong setup

## 15. No-trade conditions

Do not trade when any of these apply:

1. price is in fair zone and no sweep has happened
2. breakout or breakdown happens on very small range and immediately deadens
3. weekly and monthly completely conflict and chart is choppy
4. major news event or RBI/policy event is close
5. stop distance is too wide relative to target
6. move already ran too far and fresh trapped traders are not obvious

## 16. Option selection rule

For Nifty option buying in this system:

1. bullish trade -> buy nearest lower hundred CE
2. bearish trade -> buy nearest higher hundred PE

Examples:

1. Spot `24500` -> `24500 CE` or `24500 PE`
2. Spot `24498` -> `24400 CE`
3. Spot `24498` -> `24500 PE`

## 17. Session workflow

Before market:

1. mark previous day high, low, close
2. mark weekly and monthly OI clusters
3. classify broad bias: bullish, bearish, range, or conflicting
4. mark likely sweep zones
5. identify discount, fair, and inflated areas

After open:

1. observe gap behavior
2. avoid instant trade in first 3 to 5 minutes unless a clear extreme reclaim occurs
3. wait for sweep plus reclaim or rejection
4. confirm with other indices
5. take only trades with clear trapped-side logic

During trade:

1. manage around next liquidity target
2. watch for operator exit behavior
3. exit if thesis breaks, not just because of random candle noise

## 18. First-pass implementation notes

This v1 rulebook is intentionally heuristic and should be refined with data.

The first coding version should implement these modules:

1. bias engine
2. zone engine
3. liquidity-map engine
4. sweep-and-reclaim detector
5. option-chain confluence engine
6. multi-index confirmation engine
7. trade-scoring and management engine

## 19. v1 assumptions to validate in backtest

These values came from document interpretation and should be tuned:

1. fair-zone width: 0.25 percent around POC
2. weak breakout threshold: 10 to 50 points
3. strong move threshold: 0.35 percent in 15 minutes
4. stop buffer: max of 10 points or 0.08 percent
5. first target minimum: 1.5R

## 20. One-line system summary

Do not chase the first breakout. Wait for the market to show who got trapped, where the stops are, and whether price reclaimed or rejected the pain zone.
