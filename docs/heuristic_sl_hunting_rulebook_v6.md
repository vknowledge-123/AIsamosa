# Heuristic SL Hunting Rulebook v6

Source basis:

1. `numberical SL hunting by Amol.txt`
2. existing v1-v5 rulebooks in this repo
3. `chat SL 8.txt`
4. `chat SL 1.txt`
5. `chat SL 2.txt`
6. `chat_SL 3.txt`
7. `chat_sl 4.txt`
8. `chat Sl 5.txt`
9. `chat 6.txt`
10. `chat SL 7.txt`
11. `chat SL 9.txt`
12. `chat SL 10.txt`
13. `chat SL 11.txt`
14. `chat SL 12.txt`
15. `chat 13.txt`
16. `transaction SL enginners.txt`
17. `tracipt of mr amol for SL hunting.txt`

This v6 rulebook promotes the learned documents into a tighter AI-friendly format. It keeps the core SL-hunting philosophy, adds expiry and option-chain context, and now also adds clearer session-clock, reset-day, and range-farming discipline so the engine can avoid forcing trades from random 1-minute candles.

## Core Belief

Market first moves toward liquidity, trapped traders, and writer pain. Only after that does real intention become visible.

The engine should never decide from the latest candle alone. It must read:

1. previous-day structure
2. all intraday candles since morning
3. current trap or continuation context
4. option-chain pressure when available
5. the state of any pending setup or active trade

## Rule Set

`R1`: Before session start, mark previous day high, previous day low, and previous day close.

`R2`: Mark first 5-minute range, first 15-minute range, opening-range high/low, session high/low, prior-hour extremes, session VWAP or value anchor, major round numbers `00` and `50`, and obvious equal highs/lows.

`R3`: Treat previous day high, previous day low, previous day close, opening-range extremes, and session extremes as primary liquidity. Treat minor single-candle pivots as secondary unless confirmed by broader structure.

`R4`: Keep master levels stable through the session. Update acceptance, rejection, and bias; do not invent a new major level from every candle.

`R5`: Option chain alone is never enough. Use option chain plus chart structure together.

`R6`: When reading option chain, focus mainly on the closest `5-7` strikes above and below spot where premium, OI, and participation are real.

`R7`: If monthly expiry is active and nearby, monthly writer structure has higher weight. If monthly expiry is far or stale, weekly expiry has higher weight for intraday decisions.

`R8`: Strong call writing above spot suggests resistance or capped upside only when the chart also shows rejection or weak acceptance there.

`R9`: Strong put writing below spot suggests support only when the chart also shows support, absorption, or failed selling there.

`R10`: If strong writers sit on both sides close to spot, expect range, decay, or trap behavior until one side clearly feels pain.

`R11`: Ask on every setup: are writers actually in danger, or is price only moving a little near their strike?

`R12`: A small move of about `30-50` points near an obvious strike is not enough by itself to prove breakout or breakdown. Treat it as suspect until pain and follow-through appear.

`R13`: Writer pain means the strike is moving meaningfully in the money, premium is expanding fast, or displacement is strong enough that sellers are no longer comfortable.

`R14`: Writer comfort means price remains inside expected range, premiums decay, and breakout or breakdown attempts fail quickly.

`R15`: Classify the previous day before the open as bullish continuation, bullish recovery, bearish continuation, bearish cleanup, sideways distribution, or confusion.

`R16`: Read the opening through previous-day structure. Gap alone is not a signal.

`R17`: A large gap changes market psychology. After a large gap, reduce trust in the old intraday chart and let the new session structure develop before forcing bias.

`R18`: Gap down plus no meaningful buying favors continued selling. Gap down plus strong buying or reclaim favors buyer trap reversal. Gap up plus no follow-through often traps buyers near resistance.

`R19`: Use Nifty, Bank Nifty, and Sensex as cross-checks. When two or more align, confidence improves. When one diverges sharply, reduce confidence and expect trap or range behavior.

`R20`: Classify current price as `discount`, `fair`, or `inflated` relative to value, POC/VWAP, major support/resistance, and previous-day structure.

`R21`: Avoid fresh trades in the fair zone unless a major level breaks with real acceptance. Fair price is the easiest place for both-side trapping.

`R22`: Discount zone favors buy ideas only when sellers fail to continue or a reclaim appears. Inflated zone favors sell or trap ideas when buyers fail to sustain. Inflated zone can also support bullish continuation only when breakout acceptance is strong.

`R23`: Use the comfort rule: when the crowd feels very comfortable buying a breakout or selling a breakdown, trap probability rises.

`R24`: Always ask `who is trapped now` instead of only asking `what color is the candle`.

`R25`: Bullish trap thesis begins when price sweeps sell-side liquidity such as previous day low, session low, opening-range low, or equal lows.

`R26`: Bearish trap thesis begins when price sweeps buy-side liquidity such as previous day high, session high, opening-range high, or equal highs.

`R27`: A bullish reclaim is valid only when price closes back above the defended level after the sweep and follow-through or a successful retest confirms the reclaim.

`R28`: A bearish rejection is valid only when price closes back below the defended level after the sweep and follow-through or a successful retest confirms the rejection.

`R29`: Score reclaim or rejection quality from structure, speed, displacement, and follow-through. Weak reclaim or weak rejection means no trade.

`R30`: If the confirmation candle has a weak body, long wicks, mixed intent, or no follow-through, do not force an entry.

`R31`: If price breaks an obvious level slightly, attracts breakout traders, and quickly falls back inside the prior zone, treat it as a fake breakout and buyer trap.

`R32`: If price breaks an obvious support slightly, attracts breakdown traders, and quickly reclaims the level, treat it as a fake breakdown and seller trap.

`R33`: Acceptance means price holds beyond the broken level, pullbacks fail to reclaim the old zone, and continuation remains orderly.

`R34`: Rejection means price cannot hold beyond the swept level, quickly returns through it, and new candles defend the reclaim or rejection side.

`R35`: Do not short a market that is clearly accepting above value. Do not buy a market that is clearly accepting below value unless a strong opposite trap appears.

`R36`: Prefer only a small family of setups: Tier-1 sweep and reclaim, Tier-1 sweep and rejection, trap-then-trend continuation, previous-close reclaim or rejection, and accepted continuation after real pressure.

`R37`: When no trade is open, the AI may arm a pending setup instead of forcing a trade. A pending setup must include side, trigger price, trigger basis, invalidation level when possible, and reasoning.

`R38`: Valid trigger bases are explicit conditions such as `close_above`, `close_below`, `reclaim_above`, `reclaim_below`, `reject_above`, or `reject_below`. Avoid vague trigger language.

`R39`: Once a pending setup is armed, its trigger level should stay locked until it is triggered, invalidated, or intentionally replaced by a structurally better setup.

`R40`: If no active trade exists and a closed candle satisfies the armed trigger condition, the backend may enter mechanically. The AI does not need to rediscover the same entry again.

`R41`: After a trade opens, the old setup is consumed. From that point the AI becomes a trade manager, not a fresh entry hunter.

`R42`: When a trade is active, the AI should focus only on `HOLD`, `UPDATE_STOP`, `UPDATE_TARGET`, or `EXIT` unless the position is closed first.

`R43`: Tighten stop only after strong continuation in favor and the formation of a new defended zone. Do not tighten only from emotion or one noisy wick.

`R44`: Update target only when the next opposing liquidity pool has shifted further away or the session is accepting strongly enough to justify extension.

`R45`: Exit when the defended zone is lost by clean close, when a strong opposite reclaim appears, when acceptance flips against the trade, or when the trap thesis is clearly invalidated.

`R46`: Use partial or defensive trade management on double-side trap days, expiry days, or confusion sessions where both sides are repeatedly harvested.

`R47`: Expiry day and holiday-adjacent sessions often produce faster traps, sharper premium decay, and more fake directional comfort. Raise confirmation standards on those days.

`R48`: A strong reversal from an important level often marks an operator zone. Follow the operator side until price behavior shows the move is tiring or being exited.

`R49`: Operator exit is often seen after breakout or breakdown when candles slow, follow-through weakens, and the move stops hurting the trapped side further.

`R50`: If operator behavior, cross-index behavior, option-chain pressure, and chart structure all disagree, reduce confidence and prefer `NO_TRADE`.

`R51`: Never chase a move just because one candle is large. First decide whether that candle created real acceptance or only harvested stops.

`R52`: If the market is building repeated support before a larger fall, or repeated resistance before a larger rise, assume the market may still be preparing the real trap.

`R53`: Previous day close is a major participation and confusion reference. Around it, expect balance, chop, or trap behavior unless price clearly accepts away from it.

`R54`: If a move already removed one side strongly and cleanly, do not assume the same side still offers fuel. Weak removal leaves trapped traders alive; strong removal reduces future edge from that same trap.

`R55`: The engine should prefer no trade when the move is extended, the setup sits only on weak liquidity, or the session is dominated by fair-value churn.

`R56`: The engine should not assign high confidence only because of a strong latest candle. Confidence must come from level quality, trapped-side clarity, acceptance or rejection quality, opening context, and room to next liquidity.

`R57`: For Nifty option execution in this simulator, use the strike-selection rule already defined by the app. Direction must come from structure, not from strike convenience.

`R58`: The one-line decision test is: map the real liquidity, identify who is trapped or comfortable, confirm whether price is accepting or rejecting the move, then trade only when the trap thesis becomes clear and executable.

`R59`: If no mapped liquidity cluster, stop pool, or obvious crowding is present, expect weak edge. No obvious stops usually means no meaningful SL-hunting move.

`R60`: Use session clock gates. First `5-15` minutes are mainly for discovery and mapping, first `15-45` minutes are the primary sweep-and-reclaim window, continuation trades are best in the next `45-90` minutes only after the opening trap is resolved, midday needs stricter filters, and late session should favor quicker exits or flattening over fresh ambitious entries.

`R61`: If the first `15` minutes sweep both opening-range high and opening-range low and neither side sustains beyond roughly `0.30 ATR`, classify the session as a double-hunt candidate. If price also stays near prior value or fair value, downgrade it further to range SL-farming.

`R62`: On double-hunt or range SL-farming sessions, reduce size, cut extension expectations, avoid pyramiding, and prefer first or second objectives over heroic trend targets.

`R63`: After reclaim or rejection confirmation, valid execution styles are close-entry, next-bar trigger, or shallow retest. Regardless of style, place the stop beyond the sweep with a buffer; do not keep the stop exactly at the obvious hunt low or high.

`R64`: On large-gap or event-reset days, wait roughly `15-30` minutes, remap the opening range, and let the new chart prove itself before trusting prior micro structure. Apply this even more strictly on expiry or scheduled event sessions.

`R65`: Reject signals when no mapped liquidity was actually swept, when reclaim happens inside fair-value midrange without displacement, or when quote quality, spread quality, or nearby data instability makes the setup unreliable.

`R66`: If the trade thesis breaks, exit fast, do not average down, and do not convert a failed SL-hunt into a hope trade.

`R67`: On a flat or slight gap-up open after strong prior bullish structure, an initial dip or rejection is often liquidity creation, not immediate bearish intent. Avoid chasing the first breakout there; prefer the recovery only after sellers are activated and price starts reclaiming.

`R68`: Market needs both sides to create a meaningful trap move. If the tape is truly one-sided with no opposing participation, avoid forcing classic SL-hunt logic and wait for two-sided liquidity to appear.

`R69`: If the market behaves opposite to the pre-plan, do not force the old thesis. Pause, wait for fresh structure, and rebuild the trade map from the new evidence.

`R70`: If two indices fall or weaken while one index holds much stronger, treat that relative-strength divergence as a possible trap setup rather than assuming all three must collapse together.

`R71`: After both day low and day high are swept, do not trust the first directional push. Expect re-tests, fake moves, range behavior, and a second trap before assuming a clean trend has started.

`R72`: A level is not resistance until rejection appears, and not support until defense appears. If price is accepting above a prior resistance or below a prior support, treat that level as a breakout or breakdown base instead of blindly fading it.

`R73`: Do not short strength just because price reached a known resistance zone. Short only after greed, spike, or sweep is followed by visible rejection. Do not buy weakness just because price touched support unless the reclaim or defense is real.

`R74`: On active trades, do not exit automatically at the first nearby liquidity touch if the market is still accepting in favor of the position. First decide whether that liquidity was only touched, fully harvested, or being accepted through, then hold, trail, or exit accordingly.

`R75`: Slow grind, controlled absorption, or steady acceptance is not the same as a trap. If price advances without sharp rejection, without a fast reclaim failure, and without a clear sweep reversal, prefer continuation logic or no trade over forced counter-trend calls.

`R76`: Treat the open as validation, not prediction. Gap and opening candle behavior confirm, weaken, or trap the pre-market thesis; they do not automatically replace it.

`R77`: Classify liquidity into at least three buckets: old stop liquidity at known highs and lows, induced liquidity created after fake breaks or fresh breakout entries, and future liquidity from profit holders whose stops will trail later. Do not treat all liquidity as the same.

`R78`: Market often creates liquidity before hunting it. When a breakout, breakdown, or comfort move invites fresh late participants, mark that new positioning as future fuel for the next trap.

`R79`: Prefer reaction over candle-color thinking. A sweep becomes tradable because of the reaction after the level is taken, such as reclaim, rejection, absorption, or continuation failure, not because one candle simply closed green or red.

`R80`: If a very large gap opens beyond the main mapped upside or downside liquidity pool, assume much of that obvious liquidity may already be cleared at the open. In that case, avoid blind continuation entries and look first for exhaustion, reset behavior, or fresh acceptance before committing.

`R81`: Read the final `15-30` minutes of the previous session as part of the next-day bias engine. Strong late momentum, rejection, chop, or indecisive middle close can reveal whether buyers, sellers, or only smart money were positioning into the close.

`R82`: A psychological trap close happens when price ends near a key level, rejects on both sides, and finally closes near the middle of the range. Treat that as uncertainty for the crowd, possible smart-money positioning, and a warning not to assume the next-session breakout direction blindly.

`R83`: Use the first opening candle as a confirmation filter for the gap, not as a standalone trade signal. Gap down plus red candle supports bearish continuation, gap up plus green candle supports bullish continuation, and gap-candle conflict raises trap risk and lowers immediate confidence.

`R84`: Support and resistance are directional, not absolute. In a bearish bias, rejection zones above matter more than weak supports below. In a bullish bias, defended accumulation zones below matter more than weak resistance overhead.

`R85`: SL presence does not guarantee immediate execution. A known stop cluster may be targeted in the same session, after additional liquidity is created, or even in the next `1-2` sessions. Wait for readiness and confirmation instead of forcing the timing.

`R86`: A correct directional idea can still lose because of path uncertainty. Market may move opposite first, create more liquidity, stop out early traders, and only then make the real move. Allow limited re-entry only when the setup family still remains valid.

`R87`: Limit attempts per setup idea, not only per day. If the same SL-hunt idea fails `1-2` times or the market keeps delaying the trigger, stop trading that idea and wait for a new structure.

`R88`: Do not assume a gap must fill. A gap target becomes meaningful only if price behavior, participation, and trap logic support movement toward it. If the market avoids attracting the crowd into the gap direction, the gap may remain unfilled for now.

`R89`: Breakout quality depends on displacement, speed, participation, and reaction at the level, not on whether the chart is `1m`, `5m`, or `15m`. A strong fast `1m` move can be more meaningful than a weak slow `5m` breakout.

`R90`: Retracements often exist to remove weak holders, invite fresh late entries, or rebuild liquidity. Do not treat every retracement against the trade as trend failure; first decide whether it is only participation-building inside the same thesis.

`R91`: Sideways sessions are overtrading traps. When the market is clearly sideways, cap activity even more aggressively, prefer `1-2` trades at most, and avoid repeated breakout or breakdown attempts inside the same range.

`R92`: Classify the first meaningful move of the session as either `strong intent` or `weak intent`. Strong intent means displacement, pressure on writers, and continuation after break. Weak intent means slow grind, overlapping candles, small bodies, and no panic. Weak intent usually means stops are not yet cleared and a later trap is more likely.

`R93`: Track stop availability in three states: `cleared`, `partially cleared`, and `untouched`. A small poke through a level that traps late traders but leaves earlier traders still holding means stops are only partially cleared and a second push remains possible.

`R94`: Grade every trap as `weak`, `tradable`, or `explosive`. Weak traps happen inside chop with slow reclaim and no displacement. Tradable traps show clean sweep plus timely reclaim. Explosive traps combine important liquidity, sharp rejection, structure shift, and cross-index or options confirmation. Prefer trades only when trap quality is at least tradable.

`R95`: Predict day type early from the interaction of first move, stop availability, and reclaim behavior. Strong first move plus shallow pullbacks suggests trend day. Failed first move plus second sweep suggests double-side or trap-then-trend day. Repeated sweeps with no displacement suggest chop or mismatch day and reduced activity.

`R96`: After a calm, narrow, or compressed prior day, the next session can hide its real trap in comfort positioning rather than obvious chart levels. Range traders, option sellers, and quiet-day buyers or sellers may be the real target, so flat-to-strong opening conditions after compression deserve extra attention for directional expansion.

`R97`: Treat `slow-move trap continuation` as a distinct pattern. A slow orderly move can attract many late entrants, a sharp shakeout can clear them, and a recovery in the original direction can become the continuation entry. Slow trend does not always mean weakness; sometimes it is only participation-building before the next push.

`R98`: A stop-sweep setup is only useful if the move after it is large enough to justify the trade. If the market gives the sweep but only a small weak response with poor reward-to-risk, treat the setup as incomplete or low quality rather than forcing full conviction.

`R99`: Keep the market-bias engine separate from the trade-management engine. Before entry, define the invalidation level, the first protection point, the partial-booking plan if any, and the full-exit condition. Good analysis without pre-decided management often turns into fear-based exits.

`R100`: For genuine breakout continuation, prefer `break -> retest -> hold -> entry` over buying or selling the breakout candle itself. If the break is strong, retests hold, and no rejection appears, continuation can be traded. If the move is already extended by roughly `40-60` points without retest, late entry often turns the trader into liquidity.

`R101`: Do not join an opening move late when the market is already sharply stretched and all major indices are moving together. Late entrants after a hard opening expansion often become the next liquidity pocket, so the better setup may be the relief retracement, trap, or reversal instead of continued chasing.

`R102`: When one side becomes overcrowded because of news, gap enthusiasm, all-time-high breakout comfort, or obvious momentum chasing, the less crowded side often holds the next profit potential. Strong-looking bullish price can still be a sell setup, and strong-looking bearish price can still be a buy setup, if the nearest fresh liquidity sits with the crowded side.
