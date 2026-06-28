from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time as dt_time

from app.schemas import Candle, StrategyContext, TradeAction, TradeDecision
from app.services.heuristic_engine import HeuristicDecisionEngine


@dataclass(frozen=True)
class LiquidityPool:
    label: str
    side: str
    price: float
    weight: float
    source: str


@dataclass(frozen=True)
class SweepCandidate:
    side: str
    pool: LiquidityPool
    score: float
    sweep_extreme: float
    reason: str


def decide_nifty(
    engine: HeuristicDecisionEngine,
    context: StrategyContext,
    *,
    current_trade_price: float | None = None,
) -> TradeDecision:
    if context.active_trade is not None:
        return engine.decide(context, current_trade_price=current_trade_price)
    return _decide_simplified_liquidity_nifty(context)


def _decide_simplified_liquidity_nifty(context: StrategyContext) -> TradeDecision:
    if not context.session_candles:
        return _no_trade("NIFTY liquidity strategy is waiting for session candles.", "nifty_liquidity_warmup")
    pools = _build_liquidity_pools(context)
    if not pools:
        return _no_trade("NIFTY liquidity strategy found no 5-session liquidity pools yet.", "nifty_liquidity_warmup")

    latest = context.current_candle
    recent = context.session_candles[-3:]
    opening_candle = context.session_candles[0]
    long_candidate = _best_sweep_candidate(recent, pools, side="long", opening_candle=opening_candle)
    short_candidate = _best_sweep_candidate(recent, pools, side="short", opening_candle=opening_candle)
    bias_side, bias_note = _first_two_hour_bias(context)
    if bias_side == "long" and long_candidate is not None:
        selected = long_candidate
    elif bias_side == "short" and short_candidate is not None:
        selected = short_candidate
    elif long_candidate and short_candidate:
        selected = long_candidate if long_candidate.score >= short_candidate.score else short_candidate
    elif bias_side == "long" and short_candidate is not None:
        return _no_trade(
            f"NIFTY first-2-hour bias blocks short: {bias_note}. Existing liquidity rules found a short sweep, "
            "but until 11:15 only long-side setups are allowed for this market pattern.",
            "nifty_first_two_hour_bias_block",
        )
    elif bias_side == "short" and long_candidate is not None:
        return _no_trade(
            f"NIFTY first-2-hour bias blocks long: {bias_note}. Existing liquidity rules found a long reclaim, "
            "but until 11:15 only short-side setups are allowed for this market pattern.",
            "nifty_first_two_hour_bias_block",
        )
    else:
        selected = long_candidate or short_candidate

    if selected is None:
        return _no_trade(
            _trend_without_sweep_reason(context, pools),
            "nifty_liquidity_no_sweep",
        )

    weak_reason = _weak_operator_intent_reason(context, selected, pools)
    if weak_reason:
        return _no_trade(weak_reason, "nifty_liquidity_weak_operator_intent")

    session_reason = _late_session_entry_block_reason(context, selected, pools)
    if session_reason:
        return _no_trade(session_reason, "nifty_liquidity_late_session_filter")

    opposite_room = _room_to_opposite_liquidity(latest.close, pools, selected.side)
    minimum_room = max(_atr(context.session_candles[-20:]) * 0.8, 25.0)
    if opposite_room < minimum_room:
        return _no_trade(
            f"NIFTY liquidity setup skipped: {selected.reason} but room to opposite liquidity is only "
            f"{opposite_room:.2f} points; waiting for cleaner draw-on-liquidity.",
            "nifty_liquidity_low_room",
        )

    atr = _atr(context.session_candles[-20:])
    buffer = max(atr * 0.25, 8.0)
    if selected.side == "long":
        invalidation = round(selected.sweep_extreme - buffer, 2)
        risk = max(latest.close - invalidation, 25.0)
        return TradeDecision(
            action=TradeAction.enter_call,
            confidence=min(0.95, selected.score / 100.0),
            reason=_entry_reason(selected, context, "sellers trapped below liquidity; market can draw toward buy-side pools", bias_note),
            decision_source="heuristic-nifty-liquidity",
            option_type="CE",
            invalidation_level=invalidation,
            target_spot_price=round(latest.close + max(opposite_room, risk * 2.0), 2),
            first_target_price=round(latest.close + max(risk, min(opposite_room, risk * 1.2)), 2),
            market_state="nifty_seller_trap_liquidity_reclaim",
            setup_score=round(selected.score, 1),
            setup_type="nifty_5session_sellside_sweep_reclaim",
            rule_ids_used=["NIFTY-5D-LIQUIDITY", "NIFTY-3M-SWINGS", "NIFTY-SWEEP-RECLAIM", "NIFTY-TRAP-FIRST"],
        )

    invalidation = round(selected.sweep_extreme + buffer, 2)
    risk = max(invalidation - latest.close, 25.0)
    return TradeDecision(
        action=TradeAction.enter_put,
        confidence=min(0.95, selected.score / 100.0),
        reason=_entry_reason(selected, context, "buyers trapped above liquidity; market can draw toward sell-side pools", bias_note),
        decision_source="heuristic-nifty-liquidity",
        option_type="PE",
        invalidation_level=invalidation,
        target_spot_price=round(latest.close - max(opposite_room, risk * 2.0), 2),
        first_target_price=round(latest.close - max(risk, min(opposite_room, risk * 1.2)), 2),
        market_state="nifty_buyer_trap_liquidity_rejection",
        setup_score=round(selected.score, 1),
        setup_type="nifty_5session_buyside_sweep_rejection",
        rule_ids_used=["NIFTY-5D-LIQUIDITY", "NIFTY-3M-SWINGS", "NIFTY-SWEEP-REJECTION", "NIFTY-TRAP-FIRST"],
    )


def _entry_reason(candidate: SweepCandidate, context: StrategyContext, trap_text: str, bias_note: str | None = None) -> str:
    latest = context.current_candle
    bias_text = f" First-2-hour bias: {bias_note}." if bias_note else ""
    return (
        f"NIFTY simplified liquidity heuristic starts with trap map: {trap_text}. "
        f"{candidate.reason} Latest close {latest.close:.2f}; score {candidate.score:.1f}/100. "
        "Decision uses only last 5 trading sessions daily liquidity, 3-minute swing/equal liquidity, "
        f"previous-day high/low, and 100-point round-number bands.{bias_text}"
    )


def _build_liquidity_pools(context: StrategyContext) -> list[LiquidityPool]:
    pools: list[LiquidityPool] = []
    previous = _last_n_sessions(context.previous_day_candles, 5)
    for session_day, candles in previous:
        high = max(candle.high for candle in candles)
        low = min(candle.low for candle in candles)
        day_label = session_day.strftime("%d %b")
        weight = 34.0 if session_day == previous[-1][0] else 26.0
        pools.append(LiquidityPool(f"{day_label} day high", "buy", round(high, 2), weight, "daily"))
        pools.append(LiquidityPool(f"{day_label} day low", "sell", round(low, 2), weight, "daily"))

    if context.previous_day.high:
        pools.append(LiquidityPool("Previous day high", "buy", round(context.previous_day.high, 2), 42.0, "pdh"))
    if context.previous_day.low:
        pools.append(LiquidityPool("Previous day low", "sell", round(context.previous_day.low, 2), 42.0, "pdl"))

    prior_session = context.session_candles[:-1]
    if len(prior_session) >= 3:
        pools.append(LiquidityPool("Current day high", "buy", round(max(candle.high for candle in prior_session), 2), 44.0, "day-high"))
        pools.append(LiquidityPool("Current day low", "sell", round(min(candle.low for candle in prior_session), 2), 44.0, "day-low"))

    three_minute = _aggregate_candles([candle for _, candles in previous for candle in candles], 3)
    pools.extend(_swing_pools(three_minute, lookback=5))
    pools.extend(_equal_high_low_pools(three_minute, tolerance=max(_atr(three_minute[-80:]) * 0.15, 6.0)))
    pools.extend(_round_number_pools(context))
    return _dedupe_pools(pools)


def _first_two_hour_bias(context: StrategyContext) -> tuple[str | None, str | None]:
    current_time = context.current_candle.timestamp.time()
    if current_time < dt_time(9, 15) or current_time > dt_time(11, 15):
        return None, None
    previous_sessions = _last_n_sessions(context.previous_day_candles, 1)
    if not previous_sessions or not context.session_candles:
        return None, None
    _, previous = previous_sessions[-1]
    if not previous:
        return None, None
    day_open = previous[0].open
    day_close = previous[-1].close
    day_move = day_close - day_open
    if abs(day_move) < 5.0:
        return None, None

    last_2h = _previous_last_two_hours(previous)
    last_2h_open = last_2h[0].open if last_2h else day_open
    last_2h_close = last_2h[-1].close if last_2h else day_close
    last_2h_move = last_2h_close - last_2h_open
    flow_threshold = max(_atr(previous[-80:]) * 0.35, 12.0)
    last_2h_flow = "bullish" if last_2h_move >= flow_threshold else "bearish" if last_2h_move <= -flow_threshold else "neutral"

    gap_points = context.session_candles[0].open - day_close
    gap_type = "gap_up" if gap_points > 30.0 else "gap_down" if gap_points < -30.0 else "flat"
    day_colour = "green" if day_move > 0 else "red"

    if day_colour == "green":
        bias = "long"
        scenario = "green previous day keeps first-2-hour long bias"
        if last_2h_flow == "bearish" and gap_type == "gap_down":
            bias = "short"
            scenario = "green day but bearish last-2h fall plus gap-down open flips first-2-hour bias short"
        elif last_2h_flow == "bearish" and gap_type == "flat":
            scenario = "green day with bearish last-2h flow and flat open expects seller trap before long continuation"
        elif last_2h_flow == "bearish" and gap_type == "gap_up":
            scenario = "green day with bearish last-2h flow and gap-up open means sellers are already trapped; prefer long after retracement"
        elif gap_type == "gap_down":
            scenario = "green day with gap-down open means buyers are trapped early; prefer recovery longs after liquidity rules confirm"
        elif gap_type == "gap_up":
            scenario = "green day with gap-up open can invite BTST profit booking first, but first-2-hour bias stays long"
    else:
        bias = "short"
        scenario = "red previous day keeps first-2-hour short bias"
        if last_2h_flow == "bullish" and gap_type == "gap_up":
            bias = "long"
            scenario = "red day but bullish last-2h recovery plus gap-up open flips first-2-hour bias long"
        elif last_2h_flow == "bullish" and gap_type == "flat":
            scenario = "red day with bullish last-2h flow and flat open expects buyer trap before short continuation"
        elif last_2h_flow == "bullish" and gap_type == "gap_down":
            scenario = "red day with bullish last-2h flow and gap-down open means buyers are trapped; prefer short after retracement"
        elif gap_type == "gap_up":
            scenario = "red day with gap-up open means sellers are trapped early; prefer rejection shorts after liquidity rules confirm"
        elif gap_type == "gap_down":
            scenario = "red day with gap-down open can invite seller profit booking first, but first-2-hour bias stays short"

    note = (
        f"{scenario}; previous day {day_colour} ({day_move:+.2f}), "
        f"last-2h flow {last_2h_flow} ({last_2h_move:+.2f}), open {gap_type} ({gap_points:+.2f})"
    )
    return bias, note


def _previous_last_two_hours(previous: list[Candle]) -> list[Candle]:
    if not previous:
        return []
    close_time = previous[-1].timestamp
    cutoff_minutes = close_time.hour * 60 + close_time.minute - 120
    sliced = [
        candle
        for candle in previous
        if candle.timestamp.hour * 60 + candle.timestamp.minute >= cutoff_minutes
    ]
    return sliced or previous[-120:]


def _last_n_sessions(candles: list[Candle], count: int) -> list[tuple[date, list[Candle]]]:
    buckets: dict[date, list[Candle]] = {}
    for candle in candles:
        buckets.setdefault(candle.timestamp.date(), []).append(candle)
    sessions = [(day, sorted(items, key=lambda candle: candle.timestamp)) for day, items in sorted(buckets.items())]
    return sessions[-count:]


def _aggregate_candles(candles: list[Candle], interval_minutes: int) -> list[Candle]:
    if interval_minutes <= 1:
        return list(candles)
    aggregated: list[Candle] = []
    bucket: Candle | None = None
    bucket_key: tuple[date, int] | None = None
    for candle in sorted(candles, key=lambda item: item.timestamp):
        minutes = candle.timestamp.hour * 60 + candle.timestamp.minute
        key = (candle.timestamp.date(), minutes // interval_minutes)
        if bucket is None or key != bucket_key:
            if bucket is not None:
                aggregated.append(bucket)
            bucket = Candle(
                timestamp=candle.timestamp,
                open=candle.open,
                high=candle.high,
                low=candle.low,
                close=candle.close,
                volume=candle.volume,
            )
            bucket_key = key
            continue
        bucket.high = max(bucket.high, candle.high)
        bucket.low = min(bucket.low, candle.low)
        bucket.close = candle.close
        bucket.volume += candle.volume
        bucket.timestamp = candle.timestamp
    if bucket is not None:
        aggregated.append(bucket)
    return aggregated


def _swing_pools(candles: list[Candle], lookback: int) -> list[LiquidityPool]:
    pools: list[LiquidityPool] = []
    if len(candles) < lookback * 2 + 1:
        return pools
    for index in range(lookback, len(candles) - lookback):
        window = candles[index - lookback : index + lookback + 1]
        candle = candles[index]
        if candle.high >= max(item.high for item in window):
            pools.append(LiquidityPool(f"3m swing high {candle.timestamp.strftime('%d %b %H:%M')}", "buy", round(candle.high, 2), 24.0, "3m-swing"))
        if candle.low <= min(item.low for item in window):
            pools.append(LiquidityPool(f"3m swing low {candle.timestamp.strftime('%d %b %H:%M')}", "sell", round(candle.low, 2), 24.0, "3m-swing"))
    return pools[-40:]


def _equal_high_low_pools(candles: list[Candle], tolerance: float) -> list[LiquidityPool]:
    swings = _swing_pools(candles, lookback=5)
    pools: list[LiquidityPool] = []
    for side in {"buy", "sell"}:
        side_swings = [pool for pool in swings if pool.side == side]
        for index, pool in enumerate(side_swings):
            matches = [other for other in side_swings[index + 1 :] if abs(other.price - pool.price) <= tolerance]
            if matches:
                label = "Equal high liquidity" if side == "buy" else "Equal low liquidity"
                avg_price = round((pool.price + matches[-1].price) / 2.0, 2)
                pools.append(LiquidityPool(label, side, avg_price, 32.0, "eqh-eql"))
    return pools[-12:]


def _round_number_pools(context: StrategyContext) -> list[LiquidityPool]:
    candles = context.previous_day_candles + context.session_candles
    if not candles:
        return []
    low = min(candle.low for candle in candles)
    high = max(candle.high for candle in candles)
    start = int(low // 100) * 100
    end = int(high // 100 + 1) * 100
    pools: list[LiquidityPool] = []
    first_session_candle = context.session_candles[0] if context.session_candles else None
    for level in range(start, end + 1, 100):
        if _ignore_open_round_liquidity(float(level), first_session_candle):
            continue
        pools.append(LiquidityPool(f"100-point round band {level} +/-25", "buy", float(level), 30.0, "round"))
        pools.append(LiquidityPool(f"100-point round band {level} +/-25", "sell", float(level), 30.0, "round"))
    return pools


def _ignore_open_round_liquidity(level: float, first_candle: Candle | None) -> bool:
    if first_candle is None:
        return False
    if first_candle.low <= level <= first_candle.high:
        return True
    return abs(level - first_candle.open) <= 50.0


def _dedupe_pools(pools: list[LiquidityPool]) -> list[LiquidityPool]:
    deduped: list[LiquidityPool] = []
    for pool in sorted(pools, key=lambda item: (-item.weight, item.side, item.price)):
        if any(existing.side == pool.side and abs(existing.price - pool.price) <= 3.0 for existing in deduped):
            continue
        deduped.append(pool)
    return sorted(deduped, key=lambda item: item.price)


def _best_sweep_candidate(recent: list[Candle], pools: list[LiquidityPool], *, side: str, opening_candle: Candle) -> SweepCandidate | None:
    latest = recent[-1]
    atr = _atr(recent)
    tolerance = max(atr * 0.2, 6.0)
    candidates: list[SweepCandidate] = []
    if side == "long":
        sell_pools = [pool for pool in pools if pool.side == "sell"]
        recent_low = min(candle.low for candle in recent)
        for pool in sell_pools:
            if _skip_primary_liquidity_pool_for_open_noise(pool, opening_candle):
                continue
            if _skip_primary_liquidity_without_round_confluence(pool):
                continue
            if pool.source == "round":
                swept = recent_low <= pool.price + 25.0
                reclaim = (
                    pool.price < latest.close <= pool.price + 25.0
                    and (latest.close > latest.open or latest.close >= pool.price + 10.0)
                )
            else:
                swept = recent_low < pool.price - tolerance
                reclaim = latest.close > pool.price
            if swept and reclaim:
                depth = max(pool.price - recent_low, 0.0)
                score = min(98.0, 52.0 + pool.weight + min(depth, 35.0) * 0.35)
                candidates.append(SweepCandidate("long", pool, score, recent_low, f"{pool.label} at {pool.price:.2f} was swept/front-run below and reclaimed"))
    else:
        buy_pools = [pool for pool in pools if pool.side == "buy"]
        recent_high = max(candle.high for candle in recent)
        for pool in buy_pools:
            if _skip_primary_liquidity_pool_for_open_noise(pool, opening_candle):
                continue
            if _skip_primary_liquidity_without_round_confluence(pool):
                continue
            if pool.source == "round":
                swept = recent_high >= pool.price - 25.0
                reject = (
                    pool.price - 25.0 <= latest.close < pool.price
                    and (latest.close < latest.open or latest.close <= pool.price - 10.0)
                )
            else:
                swept = recent_high > pool.price + tolerance
                reject = latest.close < pool.price
            if swept and reject:
                depth = max(recent_high - pool.price, 0.0)
                score = min(98.0, 52.0 + pool.weight + min(depth, 35.0) * 0.35)
                candidates.append(SweepCandidate("short", pool, score, recent_high, f"{pool.label} at {pool.price:.2f} was swept/front-run above and rejected"))
    if not candidates:
        return None
    return max(candidates, key=lambda item: item.score)


def _skip_primary_liquidity_pool_for_open_noise(pool: LiquidityPool, first_candle: Candle) -> bool:
    if pool.source == "round":
        return False
    nearest_round = round(pool.price / 100.0) * 100.0
    return abs(pool.price - nearest_round) <= 20.0 and _ignore_open_round_liquidity(nearest_round, first_candle)


def _skip_primary_liquidity_without_round_confluence(pool: LiquidityPool) -> bool:
    if pool.source == "round":
        return False
    nearest_round = round(pool.price / 100.0) * 100.0
    return abs(pool.price - nearest_round) > 20.0


def _weak_operator_intent_reason(context: StrategyContext, candidate: SweepCandidate, pools: list[LiquidityPool]) -> str | None:
    pool = candidate.pool
    recent = context.session_candles[-8:]
    latest = context.current_candle
    if pool.source != "round":
        return None

    if _round_level_is_range_farming(recent, pool.price):
        return (
            f"NIFTY round-number setup skipped: {pool.label} at {pool.price:.2f} is being traded from both sides "
            "inside the current auction, so this looks like range-farming instead of clean operator control."
        )

    supporting_pool = _nearest_supporting_pool(pool, pools, candidate.side)
    if supporting_pool is not None:
        return None

    if not _is_major_round(pool.price) and not _clean_round_sweep(candidate, latest, strict=True):
        return (
            f"NIFTY middle round-number setup skipped: {pool.label} at {pool.price:.2f} is not a major 300/500-point "
            "shelf and has no nearby PDH/PDL/day-high/day-low/swing support. Waiting for stronger operator intent."
        )

    close_distance = abs(latest.close - pool.price)
    intent_distance = max(_atr(context.session_candles[-12:]) * 0.35, 12.0)
    latest_range = max(latest.high - latest.low, 0.01)
    if candidate.side == "long":
        close_location = (latest.close - latest.low) / latest_range
        front_run_intent = candidate.sweep_extreme <= pool.price + 8.0 and latest.close >= pool.price + 12.0
        intent_ok = (latest.close >= pool.price + intent_distance and close_location >= 0.58) or front_run_intent
    else:
        close_location = (latest.high - latest.close) / latest_range
        front_run_intent = candidate.sweep_extreme >= pool.price - 8.0 and latest.close <= pool.price - 12.0
        intent_ok = (latest.close <= pool.price - intent_distance and close_location >= 0.58) or front_run_intent
    if intent_ok:
        return None
    return (
        f"NIFTY round-number setup skipped: {pool.label} at {pool.price:.2f} was touched, but price closed only "
        f"{close_distance:.2f} points away without enough displacement. Waiting for operator intent, not just a band touch."
    )


def _late_session_entry_block_reason(context: StrategyContext, candidate: SweepCandidate, pools: list[LiquidityPool]) -> str | None:
    latest = context.current_candle
    clock = latest.timestamp.time()
    if clock >= dt_time(15, 0):
        return "NIFTY fresh entry blocked after 15:00 because late-session liquidity is usually exit/adjustment flow."
    if clock < dt_time(14, 30):
        return None

    pool = candidate.pool
    major_sources = {"pdh", "pdl", "day-high", "day-low", "daily"}
    if pool.source in major_sources:
        return None
    if pool.source == "round":
        supporting_pool = _nearest_supporting_pool(pool, pools, candidate.side)
        if _is_major_round(pool.price) and _clean_round_sweep(candidate, latest, strict=True):
            return None
        if supporting_pool is not None and _clean_round_sweep(candidate, latest, strict=False):
            return None
        return (
            f"NIFTY fresh entry after 14:30 blocked: {pool.label} at {pool.price:.2f} is not PDH/PDL/day high/day low "
            "and did not show a very clean major round-number sweep."
        )
    return (
        f"NIFTY fresh entry after 14:30 blocked: {pool.label} is minor liquidity. Late-session trades need "
        "PDH/PDL/day high/day low or a very clean major round-number sweep."
    )


def _is_major_round(level: float) -> bool:
    rounded = int(round(level))
    return rounded % 300 == 0 or rounded % 500 == 0


def _clean_round_sweep(candidate: SweepCandidate, latest: Candle, *, strict: bool) -> bool:
    level = candidate.pool.price
    close_distance = abs(latest.close - level)
    sweep_depth = (
        max(level - candidate.sweep_extreme, 0.0)
        if candidate.side == "long"
        else max(candidate.sweep_extreme - level, 0.0)
    )
    latest_range = max(latest.high - latest.low, 0.01)
    if candidate.side == "long":
        same_side_close = latest.close > latest.open and (latest.close - latest.low) / latest_range >= 0.58
    else:
        same_side_close = latest.close < latest.open and (latest.high - latest.close) / latest_range >= 0.58
    required_close = 22.0 if strict else 18.0
    required_depth = 18.0 if strict else 12.0
    return same_side_close and close_distance >= required_close and sweep_depth >= required_depth


def _round_level_is_range_farming(recent: list[Candle], level: float) -> bool:
    if len(recent) < 5:
        return False
    upper_touches = sum(1 for candle in recent if candle.high >= level + 18.0)
    lower_touches = sum(1 for candle in recent if candle.low <= level - 18.0)
    closes_inside_band = sum(1 for candle in recent if level - 25.0 <= candle.close <= level + 25.0)
    return upper_touches >= 2 and lower_touches >= 2 and closes_inside_band >= max(3, len(recent) // 2)


def _nearest_supporting_pool(round_pool: LiquidityPool, pools: list[LiquidityPool], side: str) -> LiquidityPool | None:
    support_sources = {"daily", "pdh", "pdl", "day-high", "day-low", "3m-swing", "eqh-eql"}
    nearby = [
        pool
        for pool in pools
        if pool.source in support_sources
        and pool.side == round_pool.side
        and abs(pool.price - round_pool.price) <= 35.0
    ]
    if not nearby:
        return None
    return min(nearby, key=lambda item: abs(item.price - round_pool.price))


def _room_to_opposite_liquidity(close: float, pools: list[LiquidityPool], side: str) -> float:
    if side == "long":
        targets = [pool.price for pool in pools if pool.side == "buy" and pool.price > close]
        return min((price - close for price in targets), default=80.0)
    targets = [pool.price for pool in pools if pool.side == "sell" and pool.price < close]
    return min((close - price for price in targets), default=80.0)


def _trend_without_sweep_reason(context: StrategyContext, pools: list[LiquidityPool]) -> str:
    recent = context.session_candles[-5:]
    if len(recent) >= 4:
        higher_highs = all(recent[index].high > recent[index - 1].high for index in range(1, len(recent)))
        lower_lows = all(recent[index].low < recent[index - 1].low for index in range(1, len(recent)))
        if higher_highs:
            return (
                "NIFTY liquidity strategy sees higher highs, but no buy-side liquidity sweep/rejection or "
                "sell-side sweep/reclaim has completed. Avoiding trend-chase without trapped traders."
            )
        if lower_lows:
            return (
                "NIFTY liquidity strategy sees lower lows, but no sell-side liquidity sweep/reclaim or "
                "buy-side sweep/rejection has completed. Avoiding trend-chase without trapped traders."
            )
    nearest = sorted(pools, key=lambda pool: abs(pool.price - context.current_candle.close))[:4]
    labels = ", ".join(f"{pool.label} {pool.price:.2f}" for pool in nearest)
    return (
        "NIFTY liquidity strategy starts with who is trapped: no retail stop pool has been swept and reclaimed/rejected yet. "
        f"Nearest mapped liquidity: {labels}."
    )


def _no_trade(reason: str, setup_type: str) -> TradeDecision:
    return TradeDecision(
        action=TradeAction.no_trade,
        confidence=0.3,
        reason=reason,
        decision_source="heuristic-nifty-liquidity",
        setup_type=setup_type,
        market_state="nifty_liquidity_wait",
        setup_score=30.0,
        rule_ids_used=["NIFTY-5D-LIQUIDITY", "NIFTY-NO-SWEEP-NO-TRADE"],
    )


def _atr(candles: list[Candle]) -> float:
    if not candles:
        return 20.0
    return sum(max(candle.high - candle.low, 0.01) for candle in candles) / len(candles)
