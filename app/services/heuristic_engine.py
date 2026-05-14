from __future__ import annotations

from datetime import date, timedelta
from dataclasses import dataclass, field
import math
from statistics import median

from app.schemas import Candle, PendingSetup, StrategyContext, TradeAction, TradeDecision


@dataclass
class SweepEvent:
    side: str
    level_label: str
    level_price: float
    sweep_index: int
    reclaim_index: int | None
    trigger_index: int | None
    sweep_price: float
    defended_level: float
    trigger_price: float
    invalidation_level: float
    primary: bool
    quality: str
    notes: list[str] = field(default_factory=list)


@dataclass
class SetupCandidate:
    setup_type: str
    direction: str
    option_type: str
    trigger_basis: str
    trigger_price: float
    invalidation_level: float
    defended_level: float
    target_spot_price: float
    first_target_price: float
    score: float
    ready_to_enter: bool
    notes: list[str]
    rule_ids: list[str]
    event: SweepEvent


@dataclass
class Observation:
    session_phase: str
    day_type: str
    value_state: str
    previous_day_bias: str
    prior_close_psychology: str
    opening_confirmation: str
    stop_availability: str
    operator_bias: str
    crowding_bias: str
    vwap: float
    opening_range_high: float
    opening_range_low: float
    first_fifteen_high: float
    first_fifteen_low: float
    prior_hour_high: float
    prior_hour_low: float
    session_high: float
    session_low: float
    prior_session_high: float
    prior_session_low: float
    atr: float
    overlap_ratio: float
    gap: float
    strong_intent: bool
    weak_intent: bool
    expiry_session: bool
    large_gap_reset: bool
    compression_day: bool
    two_sided_participation: bool
    previous_close_reclaim_long_ready: bool
    previous_close_reclaim_short_ready: bool
    previous_close_touched: bool
    mapped_buy_liquidity: list[tuple[str, float, bool]]
    mapped_sell_liquidity: list[tuple[str, float, bool]]
    buy_sweeps: list[SweepEvent]
    sell_sweeps: list[SweepEvent]


class HeuristicDecisionEngine:
    def __init__(self) -> None:
        self.enter_threshold = 68.0
        self.arm_threshold = 52.0
        self.pending_setup_max_bars = 10
        self._current_session_date: date | None = None
        self._trace_entries: list[dict] = []
        self._narrative_events: list[dict] = []
        self._narrative_keys: set[tuple[str, str, str]] = set()

    def reset_session(self) -> None:
        self._current_session_date = None
        self._trace_entries = []
        self._narrative_events = []
        self._narrative_keys = set()

    def trace_snapshot(self) -> list[dict]:
        return list(reversed(self._trace_entries[-120:]))

    def narrative_snapshot(self) -> list[dict]:
        return list(reversed(self._narrative_events[-80:]))

    def _ensure_session(self, current_session_date: date) -> None:
        if self._current_session_date != current_session_date:
            self._current_session_date = current_session_date
            self._trace_entries = []
            self._narrative_events = []
            self._narrative_keys = set()

    def decide(self, context: StrategyContext, current_trade_price: float | None = None) -> TradeDecision:
        self._ensure_session(context.current_candle.timestamp.date())
        observation = self.observe(context)
        candidates = self.build_candidates(context, observation)
        if context.active_trade is not None:
            decision = self.manage_active_trade(context, observation, current_trade_price)
        else:
            decision = self.decide_entry(context, observation, candidates)
        self.record_trace(context, observation, decision, candidates)
        self.record_narrative(context, observation, decision)
        return decision

    def observe(self, context: StrategyContext) -> Observation:
        session = context.session_candles
        previous = context.previous_day
        current = context.current_candle
        first_five = session[: min(5, len(session))]
        first_fifteen = session[: min(15, len(session))]
        prior_hour = session[-60:] if len(session) > 60 else session
        ranges = [max(candle.high - candle.low, 0.01) for candle in session[-20:]] or [1.0]
        atr = median(ranges)
        vwap_denominator = sum(max(candle.volume, 1.0) for candle in session)
        vwap = sum(candle.close * max(candle.volume, 1.0) for candle in session) / vwap_denominator
        overlap_ratio = self._overlap_ratio(session[-20:])
        gap = session[0].open - previous.close if previous.close else 0.0
        previous_close_touched = False
        previous_close_reclaim_long_ready = False
        previous_close_reclaim_short_ready = False
        if previous.close:
            tolerance = max(atr * 0.08, 0.15)
            previous_close_touched = any(candle.low <= previous.close + tolerance and candle.high >= previous.close - tolerance for candle in session)
            recent_three = session[-3:] if len(session) >= 3 else session
            previous_close_reclaim_long_ready = previous_close_touched and current.close > previous.close and all(
                candle.close >= previous.close - tolerance * 0.3 for candle in recent_three
            )
            previous_close_reclaim_short_ready = previous_close_touched and current.close < previous.close and all(
                candle.close <= previous.close + tolerance * 0.3 for candle in recent_three
            )

        session_range = max(candle.high for candle in session) - min(candle.low for candle in session)
        strong_intent = len(session) >= 5 and session_range > atr * 3 and abs(current.close - session[0].open) > atr * 1.5
        weak_intent = overlap_ratio > 0.58 or session_range < atr * 2.2
        session_phase = self.classify_session_phase(len(session))
        day_type = self.classify_day_type(context, atr, overlap_ratio, gap)
        value_state = self.classify_value_state(current.close, previous.close, vwap, atr)
        previous_day_bias = self.classify_previous_day_bias(context.previous_day_candles)
        prior_close_psychology = self.classify_prior_close_psychology(context.previous_day_candles)
        opening_confirmation = self.classify_opening_confirmation(session, gap, atr)
        expiry_session = current.timestamp.weekday() == 3
        large_gap_reset = abs(gap) > atr * 1.1
        compression_day = self.is_compression_day(context.previous_day_candles, atr)
        two_sided_participation = self.detect_two_sided_participation(session)
        operator_bias = self.classify_operator_bias(context.operator_zones, current.close, atr)
        crowding_bias = self.classify_crowding_bias(session, current, atr, value_state)
        mapped_buy_liquidity, mapped_sell_liquidity = self.build_liquidity_maps(session, current.close, atr)

        buy_sweeps = self.detect_sweeps(
            session,
            side="buy",
            previous_day_level=previous.high,
            opening_level=max(candle.high for candle in first_five) if first_five else current.high,
            first_fifteen_level=max(candle.high for candle in first_fifteen) if first_fifteen else current.high,
            prior_hour_level=max(candle.high for candle in prior_hour) if prior_hour else current.high,
            session_reference=max(candle.high for candle in session[:-1]) if len(session) > 1 else current.high,
            atr=atr,
            extra_levels=mapped_buy_liquidity,
        )
        sell_sweeps = self.detect_sweeps(
            session,
            side="sell",
            previous_day_level=previous.low,
            opening_level=min(candle.low for candle in first_five) if first_five else current.low,
            first_fifteen_level=min(candle.low for candle in first_fifteen) if first_fifteen else current.low,
            prior_hour_level=min(candle.low for candle in prior_hour) if prior_hour else current.low,
            session_reference=min(candle.low for candle in session[:-1]) if len(session) > 1 else current.low,
            atr=atr,
            extra_levels=mapped_sell_liquidity,
        )
        stop_availability = self.assess_stop_availability(buy_sweeps, sell_sweeps)

        return Observation(
            session_phase=session_phase,
            day_type=day_type,
            value_state=value_state,
            previous_day_bias=previous_day_bias,
            prior_close_psychology=prior_close_psychology,
            opening_confirmation=opening_confirmation,
            stop_availability=stop_availability,
            operator_bias=operator_bias,
            crowding_bias=crowding_bias,
            vwap=vwap,
            opening_range_high=max(candle.high for candle in first_five) if first_five else current.high,
            opening_range_low=min(candle.low for candle in first_five) if first_five else current.low,
            first_fifteen_high=max(candle.high for candle in first_fifteen) if first_fifteen else current.high,
            first_fifteen_low=min(candle.low for candle in first_fifteen) if first_fifteen else current.low,
            prior_hour_high=max(candle.high for candle in prior_hour) if prior_hour else current.high,
            prior_hour_low=min(candle.low for candle in prior_hour) if prior_hour else current.low,
            session_high=max(candle.high for candle in session),
            session_low=min(candle.low for candle in session),
            prior_session_high=max(candle.high for candle in session[:-1]) if len(session) > 1 else current.high,
            prior_session_low=min(candle.low for candle in session[:-1]) if len(session) > 1 else current.low,
            atr=atr,
            overlap_ratio=overlap_ratio,
            gap=gap,
            strong_intent=strong_intent,
            weak_intent=weak_intent,
            expiry_session=expiry_session,
            large_gap_reset=large_gap_reset,
            compression_day=compression_day,
            two_sided_participation=two_sided_participation,
            previous_close_reclaim_long_ready=previous_close_reclaim_long_ready,
            previous_close_reclaim_short_ready=previous_close_reclaim_short_ready,
            previous_close_touched=previous_close_touched,
            mapped_buy_liquidity=mapped_buy_liquidity,
            mapped_sell_liquidity=mapped_sell_liquidity,
            buy_sweeps=buy_sweeps,
            sell_sweeps=sell_sweeps,
        )

    def classify_session_phase(self, candle_count: int) -> str:
        if candle_count <= 5:
            return "discovery"
        if candle_count <= 15:
            return "opening-map"
        if candle_count <= 45:
            return "primary-trap-window"
        if candle_count <= 90:
            return "continuation-window"
        if candle_count <= 210:
            return "midday"
        return "late-session"

    def classify_day_type(self, context: StrategyContext, atr: float, overlap_ratio: float, gap: float) -> str:
        session = context.session_candles
        current = context.current_candle
        previous = context.previous_day
        if len(session) < 6:
            return "discovery"

        opening_high = max(candle.high for candle in session[: min(15, len(session))])
        opening_low = min(candle.low for candle in session[: min(15, len(session))])
        swept_opening_high = any(candle.high > opening_high + atr * 0.08 for candle in session[15:])
        swept_opening_low = any(candle.low < opening_low - atr * 0.08 for candle in session[15:])

        if abs(gap) > atr * 1.1:
            if previous.close and ((gap > 0 and current.close < previous.close) or (gap < 0 and current.close > previous.close)):
                return "gap-reversal"
            if abs(current.close - session[0].open) > atr * 1.2:
                return "gap-and-go"

        if swept_opening_high and swept_opening_low and overlap_ratio > 0.48:
            return "double-side-hunt"
        if overlap_ratio > 0.62:
            return "range/sl-farming"
        if abs(current.close - session[0].open) > atr * 1.8 and overlap_ratio < 0.36:
            return "trend-day"
        return "trap-day"

    def classify_value_state(self, current_close: float, previous_close: float, vwap: float, atr: float) -> str:
        anchor = (vwap + previous_close) / 2 if previous_close else vwap
        if current_close <= anchor - atr * 0.35:
            return "discount"
        if current_close >= anchor + atr * 0.35:
            return "inflated"
        return "fair"

    def classify_previous_day_bias(self, previous_day_candles: list[Candle]) -> str:
        if not previous_day_candles:
            return "unknown"
        first = previous_day_candles[0]
        last = previous_day_candles[-1]
        day_high = max(candle.high for candle in previous_day_candles)
        day_low = min(candle.low for candle in previous_day_candles)
        day_range = max(day_high - day_low, 0.01)
        body = last.close - first.open
        close_position = (last.close - day_low) / day_range
        if abs(body) <= day_range * 0.12:
            return "sideways-distribution" if 0.35 <= close_position <= 0.65 else "confusion"
        if body > 0:
            return "bullish-continuation" if close_position >= 0.72 else "bullish-recovery"
        return "bearish-continuation" if close_position <= 0.28 else "bearish-cleanup"

    def classify_prior_close_psychology(self, previous_day_candles: list[Candle]) -> str:
        if not previous_day_candles:
            return "unknown"
        last_window = previous_day_candles[-30:] if len(previous_day_candles) >= 30 else previous_day_candles
        high = max(candle.high for candle in last_window)
        low = min(candle.low for candle in last_window)
        close = last_window[-1].close
        window_range = max(high - low, 0.01)
        midpoint = low + (window_range / 2)
        closes_near_mid = abs(close - midpoint) <= window_range * 0.12
        two_sided_reject = any(candle.high >= high - window_range * 0.08 for candle in last_window) and any(
            candle.low <= low + window_range * 0.08 for candle in last_window
        )
        if closes_near_mid and two_sided_reject:
            return "psychological-trap-close"
        if close >= high - window_range * 0.15:
            return "late-bullish-positioning"
        if close <= low + window_range * 0.15:
            return "late-bearish-positioning"
        return "balanced-close"

    def classify_opening_confirmation(self, session: list[Candle], gap: float, atr: float) -> str:
        if not session:
            return "unknown"
        opening = session[0]
        meaningful_gap = max(atr * 0.25, 0.2)
        if abs(gap) < meaningful_gap:
            return "flat-open"
        if gap > 0:
            return "gap-up-confirmed" if opening.close > opening.open else "gap-up-trap-risk"
        return "gap-down-confirmed" if opening.close < opening.open else "gap-down-trap-risk"

    def detect_two_sided_participation(self, session: list[Candle]) -> bool:
        if len(session) < 4:
            return False
        recent = session[-8:]
        up_closes = sum(1 for candle in recent if candle.close > candle.open)
        down_closes = sum(1 for candle in recent if candle.close < candle.open)
        return up_closes >= 2 and down_closes >= 2

    def is_compression_day(self, previous_day_candles: list[Candle], current_atr: float) -> bool:
        if not previous_day_candles:
            return False
        prev_range = max(candle.high for candle in previous_day_candles) - min(candle.low for candle in previous_day_candles)
        prev_ranges = [max(candle.high - candle.low, 0.01) for candle in previous_day_candles[-30:]] or [current_atr]
        prev_atr = median(prev_ranges)
        return prev_range <= max(prev_atr * 5.0, current_atr * 2.0)

    def assess_stop_availability(self, buy_sweeps: list[SweepEvent], sell_sweeps: list[SweepEvent]) -> str:
        if buy_sweeps and sell_sweeps:
            return "cleared"
        if buy_sweeps or sell_sweeps:
            return "partially-cleared"
        return "untouched"

    def classify_operator_bias(self, operator_zones, current_close: float, atr: float) -> str:
        if not operator_zones:
            return "neutral"
        nearest = min(operator_zones, key=lambda zone: abs(zone.price - current_close))
        if abs(nearest.price - current_close) > max(atr * 0.8, 1.0):
            return "neutral"
        label = nearest.label.lower()
        if "demand" in label:
            return "bullish"
        if "supply" in label:
            return "bearish"
        return "neutral"

    def classify_crowding_bias(self, session: list[Candle], current: Candle, atr: float, value_state: str) -> str:
        if len(session) < 4:
            return "balanced"
        recent = session[-4:]
        up_closes = sum(1 for candle in recent if candle.close > candle.open)
        down_closes = sum(1 for candle in recent if candle.close < candle.open)
        displacement = current.close - session[0].open
        if up_closes >= 3 and displacement > atr * 1.4 and value_state == "inflated":
            return "long-comfort"
        if down_closes >= 3 and displacement < -atr * 1.4 and value_state == "discount":
            return "short-comfort"
        return "balanced"

    def build_liquidity_maps(
        self,
        session: list[Candle],
        current_close: float,
        atr: float,
    ) -> tuple[list[tuple[str, float, bool]], list[tuple[str, float, bool]]]:
        if not session:
            return [], []

        tolerance = max(atr * 0.12, 0.2)
        buy_levels: list[tuple[str, float, bool]] = []
        sell_levels: list[tuple[str, float, bool]] = []
        swing_highs, swing_lows = self._detect_session_swings(session, atr)

        high_clusters = sorted(
            self._cluster_price_levels([candle.high for candle in session], tolerance),
            key=lambda item: (item[1], item[0]),
            reverse=True,
        )
        low_clusters = sorted(
            self._cluster_price_levels([candle.low for candle in session], tolerance),
            key=lambda item: (item[1], -item[0]),
        )

        for cluster_price, touches in high_clusters[:3]:
            self._append_liquidity_level(
                buy_levels,
                label=f"Equal High Cluster ({touches} touches)",
                price=cluster_price,
                primary=True,
                tolerance=tolerance,
            )
        for cluster_price, touches in low_clusters[:3]:
            self._append_liquidity_level(
                sell_levels,
                label=f"Equal Low Cluster ({touches} touches)",
                price=cluster_price,
                primary=True,
                tolerance=tolerance,
            )

        for label, price in swing_highs[-4:]:
            self._append_liquidity_level(
                buy_levels,
                label=f"Same-Day Swing High {label}",
                price=price,
                primary=False,
                tolerance=tolerance,
            )
        for label, price in swing_lows[-4:]:
            self._append_liquidity_level(
                sell_levels,
                label=f"Same-Day Swing Low {label}",
                price=price,
                primary=False,
                tolerance=tolerance,
            )

        round_step = self._round_number_step(current_close)
        round_tolerance = max(round_step * 0.16, atr * 0.4, 0.25)
        buy_round_sources = [max(candle.high for candle in session)]
        buy_round_sources.extend(price for _, price in swing_highs[-5:])
        sell_round_sources = [min(candle.low for candle in session)]
        sell_round_sources.extend(price for _, price in swing_lows[-5:])

        for price in buy_round_sources:
            round_level = round(price / round_step) * round_step
            if abs(price - round_level) <= round_tolerance:
                self._append_liquidity_level(
                    buy_levels,
                    label=f"Round Number {round(round_level, 2):.2f}",
                    price=round_level,
                    primary=True,
                    tolerance=max(tolerance, round_tolerance * 0.6),
                )
        for price in sell_round_sources:
            round_level = round(price / round_step) * round_step
            if abs(price - round_level) <= round_tolerance:
                self._append_liquidity_level(
                    sell_levels,
                    label=f"Round Number {round(round_level, 2):.2f}",
                    price=round_level,
                    primary=True,
                    tolerance=max(tolerance, round_tolerance * 0.6),
                )

        return buy_levels[:8], sell_levels[:8]

    def _detect_session_swings(self, session: list[Candle], atr: float) -> tuple[list[tuple[str, float]], list[tuple[str, float]]]:
        if len(session) < 3:
            return [], []

        swing_highs: list[tuple[str, float]] = []
        swing_lows: list[tuple[str, float]] = []
        minimum_swing = max(atr * 0.45, 0.25)
        for index in range(1, len(session) - 1):
            previous = session[index - 1]
            candle = session[index]
            following = session[index + 1]
            if (
                candle.high >= previous.high
                and candle.high >= following.high
                and candle.high - min(previous.low, candle.low, following.low) >= minimum_swing
            ):
                swing_highs.append((candle.timestamp.strftime("%H:%M"), round(candle.high, 2)))
            if (
                candle.low <= previous.low
                and candle.low <= following.low
                and max(previous.high, candle.high, following.high) - candle.low >= minimum_swing
            ):
                swing_lows.append((candle.timestamp.strftime("%H:%M"), round(candle.low, 2)))
        return swing_highs, swing_lows

    def _cluster_price_levels(self, prices: list[float], tolerance: float) -> list[tuple[float, int]]:
        if len(prices) < 2:
            return []
        ordered = sorted(prices)
        groups: list[list[float]] = [[ordered[0]]]
        for price in ordered[1:]:
            if abs(price - groups[-1][-1]) <= tolerance:
                groups[-1].append(price)
            else:
                groups.append([price])

        clusters: list[tuple[float, int]] = []
        for group in groups:
            if len(group) >= 2:
                clusters.append((round(sum(group) / len(group), 2), len(group)))
        return clusters

    def _append_liquidity_level(
        self,
        levels: list[tuple[str, float, bool]],
        *,
        label: str,
        price: float,
        primary: bool,
        tolerance: float,
    ) -> None:
        if not price:
            return
        for existing_label, existing_price, _ in levels:
            same_family = existing_label.split()[0] == label.split()[0]
            if existing_label == label or (same_family and abs(existing_price - price) <= tolerance):
                return
        levels.append((label, round(price, 2), primary))

    def _round_number_step(self, reference_price: float) -> float:
        absolute = abs(reference_price)
        if absolute >= 500:
            return 50.0
        if absolute >= 100:
            return 10.0
        return 5.0

    def detect_sweeps(
        self,
        session: list[Candle],
        *,
        side: str,
        previous_day_level: float,
        opening_level: float,
        first_fifteen_level: float,
        prior_hour_level: float,
        session_reference: float,
        atr: float,
        extra_levels: list[tuple[str, float, bool]],
    ) -> list[SweepEvent]:
        events: list[SweepEvent] = []
        tolerance = max(atr * 0.08, 0.15)
        levels = [
            ("Previous Day High" if side == "buy" else "Previous Day Low", previous_day_level, True),
            ("Opening Range High" if side == "buy" else "Opening Range Low", opening_level, True),
            ("First 15m High" if side == "buy" else "First 15m Low", first_fifteen_level, True),
            ("Prior Hour High" if side == "buy" else "Prior Hour Low", prior_hour_level, False),
            ("Session Extreme" if side == "buy" else "Session Extreme", session_reference, False),
        ]
        levels.extend(extra_levels)
        for level_label, level_price, primary in levels:
            if not level_price:
                continue
            event = self._find_latest_sweep(session, side=side, level_label=level_label, level_price=level_price, atr=atr, tolerance=tolerance, primary=primary)
            if event is not None:
                events.append(event)
        events.sort(key=lambda item: (item.reclaim_index or item.sweep_index, item.primary), reverse=True)
        return events

    def _find_latest_sweep(
        self,
        session: list[Candle],
        *,
        side: str,
        level_label: str,
        level_price: float,
        atr: float,
        tolerance: float,
        primary: bool,
    ) -> SweepEvent | None:
        for index in range(len(session) - 1, -1, -1):
            candle = session[index]
            if side == "buy":
                swept = candle.high > level_price + tolerance
                reclaimed = candle.close < level_price
                defended_level = level_price
                trigger_price = min(candle.low, session[index].low)
                invalidation_level = max(candle.high, level_price + tolerance)
            else:
                swept = candle.low < level_price - tolerance
                reclaimed = candle.close > level_price
                defended_level = level_price
                trigger_price = max(candle.high, session[index].high)
                invalidation_level = min(candle.low, level_price - tolerance)
            if not swept:
                continue

            reclaim_index = index if reclaimed else None
            trigger_index = None
            quality = "weak"
            notes = [f"{level_label} swept."]
            if reclaim_index is None:
                for follow_index in range(index + 1, min(len(session), index + 4)):
                    follow = session[follow_index]
                    if side == "buy" and follow.close < level_price:
                        reclaim_index = follow_index
                        trigger_index = follow_index
                        quality = "tradable" if self.candle_strength(follow) >= 0.55 else "weak"
                        notes.append("Breakout trap reclaimed back below the swept high.")
                        break
                    if side == "sell" and follow.close > level_price:
                        reclaim_index = follow_index
                        trigger_index = follow_index
                        quality = "tradable" if self.candle_strength(follow) >= 0.55 else "weak"
                        notes.append("Breakdown trap reclaimed back above the swept low.")
                        break
            else:
                trigger_index = index
                quality = "tradable" if self.candle_strength(candle) >= 0.55 else "weak"
                notes.append("Sweep and reclaim happened on the same candle.")

            if reclaim_index is not None:
                trigger_candle = session[reclaim_index]
                if side == "buy":
                    trigger_price = min(trigger_candle.low, level_price - tolerance * 0.5)
                    invalidation_level = max(trigger_candle.high, level_price + atr * 0.18)
                else:
                    trigger_price = max(trigger_candle.high, level_price + tolerance * 0.5)
                    invalidation_level = min(trigger_candle.low, level_price - atr * 0.18)
                post_reclaim = session[reclaim_index : min(len(session), reclaim_index + 3)]
                if len(post_reclaim) >= 2:
                    follow = post_reclaim[-1]
                    if side == "buy" and follow.close < level_price and self.candle_strength(follow) >= 0.65:
                        quality = "explosive"
                        notes.append("Follow-through confirmed seller trap.")
                    if side == "sell" and follow.close > level_price and self.candle_strength(follow) >= 0.65:
                        quality = "explosive"
                        notes.append("Follow-through confirmed buyer trap.")

            return SweepEvent(
                side=side,
                level_label=level_label,
                level_price=level_price,
                sweep_index=index,
                reclaim_index=reclaim_index,
                trigger_index=trigger_index,
                sweep_price=candle.high if side == "buy" else candle.low,
                defended_level=defended_level,
                trigger_price=trigger_price,
                invalidation_level=invalidation_level,
                primary=primary,
                quality=quality,
                notes=notes,
            )
        return None

    def candle_strength(self, candle: Candle) -> float:
        candle_range = max(candle.high - candle.low, 0.01)
        body = abs(candle.close - candle.open)
        return body / candle_range

    def decide_entry(self, context: StrategyContext, observation: Observation, candidates: list[SetupCandidate] | None = None) -> TradeDecision:
        candidates = candidates if candidates is not None else self.build_candidates(context, observation)
        if context.pending_setup is not None:
            pending_decision = self.evaluate_pending_setup(context, observation, candidates)
            if pending_decision is not None:
                return pending_decision

        best = self.select_best_candidate(candidates)
        if best is None:
            return TradeDecision(
                action=TradeAction.no_trade,
                confidence=0.36,
                reason=(
                    f"Heuristic V2 sees {observation.day_type} conditions in {observation.session_phase} with "
                    f"{observation.value_state} pricing, but no setup reached executable quality."
                ),
                decision_source="heuristic",
                market_state=observation.day_type,
                rule_ids_used=["R21", "R29", "R30", "R50", "R55", "R56", "R65", "R95"],
            )

        if best.ready_to_enter and best.score >= self.enter_threshold:
            entry_rule_ids = list(best.rule_ids)
            if context.instrument.supports_options:
                entry_rule_ids.append("R57")
            return TradeDecision(
                action=TradeAction.enter_call if best.option_type == "CE" else TradeAction.enter_put,
                confidence=min(0.95, best.score / 100),
                reason=self._candidate_reason(best, observation, enter_now=True),
                decision_source="heuristic",
                option_type=best.option_type,
                invalidation_level=round(best.invalidation_level, 2),
                target_spot_price=round(best.target_spot_price, 2),
                first_target_price=round(best.first_target_price, 2),
                market_state=observation.day_type,
                setup_score=round(best.score, 2),
                setup_type=best.setup_type,
                rule_ids_used=list(dict.fromkeys(entry_rule_ids)),
            )

        if best.score >= self.arm_threshold:
            return TradeDecision(
                action=TradeAction.no_trade,
                confidence=min(0.9, best.score / 100),
                reason=self._candidate_reason(best, observation, enter_now=False),
                decision_source="heuristic",
                option_type=best.option_type,
                invalidation_level=round(best.invalidation_level, 2),
                target_spot_price=round(best.target_spot_price, 2),
                first_target_price=round(best.first_target_price, 2),
                market_state=observation.day_type,
                setup_score=round(best.score, 2),
                setup_type=best.setup_type,
                pending_setup_action="ARM",
                pending_setup_type=best.setup_type,
                pending_setup_direction=best.direction,
                pending_setup_trigger_price=round(best.trigger_price, 2),
                pending_setup_invalidation_level=round(best.invalidation_level, 2),
                pending_setup_trigger_basis=best.trigger_basis,
                pending_setup_notes=self._candidate_reason(best, observation, enter_now=False),
                pending_setup_option_type=best.option_type,
                rule_ids_used=list(dict.fromkeys(best.rule_ids + ["R37", "R38", "R40"])),
            )

        return TradeDecision(
            action=TradeAction.no_trade,
            confidence=min(0.75, best.score / 100),
            reason=self._candidate_reason(best, observation, enter_now=False),
            decision_source="heuristic",
            market_state=observation.day_type,
            setup_score=round(best.score, 2),
            setup_type=best.setup_type,
            rule_ids_used=list(dict.fromkeys(best.rule_ids + ["R95"])),
        )

    def build_candidates(self, context: StrategyContext, observation: Observation) -> list[SetupCandidate]:
        candidates: list[SetupCandidate] = []
        if observation.sell_sweeps:
            candidate = self.build_candidate_from_event(context, observation, observation.sell_sweeps[0], option_type="CE", direction="LONG_CALL")
            if candidate is not None:
                candidates.append(candidate)
        if observation.buy_sweeps:
            candidate = self.build_candidate_from_event(context, observation, observation.buy_sweeps[0], option_type="PE", direction="LONG_PUT")
            if candidate is not None:
                candidates.append(candidate)
        previous_close_candidates = self.build_previous_close_candidates(context, observation)
        candidates.extend(previous_close_candidates)
        return candidates

    def build_previous_close_candidates(self, context: StrategyContext, observation: Observation) -> list[SetupCandidate]:
        previous_close = context.previous_day.close
        if not previous_close or not observation.previous_close_touched:
            return []

        session = context.session_candles
        current = context.current_candle
        tolerance = max(observation.atr * 0.08, 0.15)
        candidates: list[SetupCandidate] = []
        risk = max(observation.atr * 0.9, abs(current.close - previous_close), 0.5)
        gap_up = observation.gap > tolerance
        gap_down = observation.gap < -tolerance
        recent_three = session[-3:] if len(session) >= 3 else session
        recent_body_quality = max((self.candle_strength(candle) for candle in recent_three), default=0.0)

        if observation.previous_close_reclaim_long_ready:
            score = 58.0
            notes = ["Previous day close was revisited and reclaimed.", "Gap-fill or previous-close defense is active."]
            rule_ids = ["R16", "R18", "R20", "R21", "R36", "R53", "R58", "R64", "R72", "R79", "R88", "R99"]
            if gap_up or gap_down:
                score += 10
                notes.append("Gap context makes the previous-close reclaim more meaningful.")
            if observation.opening_confirmation in {"gap-down-trap-risk", "gap-up-confirmed", "flat-open"}:
                score += 6
                rule_ids.extend(["R16", "R18", "R83"])
            if observation.previous_day_bias.startswith("bullish"):
                score += 6
                notes.append("Previous session bias still supports recovery continuation.")
                rule_ids.extend(["R15", "R81"])
            if observation.prior_close_psychology == "psychological-trap-close":
                score += 5
                notes.append("Prior close ended with crowd confusion, which supports a fresh reclaim read.")
                rule_ids.append("R82")
            if observation.compression_day and observation.strong_intent:
                score += 6
                notes.append("Compressed prior session is expanding directionally today.")
                rule_ids.extend(["R96", "R97"])
            if recent_body_quality >= 0.55:
                score += 8
            if observation.strong_intent:
                score += 8
            if observation.value_state == "discount":
                score += 6
            if observation.crowding_bias == "short-comfort":
                score += 5
                notes.append("Short-side comfort has built up, so squeeze risk helps the long idea.")
                rule_ids.append("R102")
            trigger_reference = recent_three[:-1] if len(recent_three) > 1 else recent_three
            trigger_price = max(candle.high for candle in trigger_reference)
            ready_to_enter = current.close > trigger_price and current.close > current.open
            if ready_to_enter:
                score += 10
            target_spot = self.next_upside_target(context, current.close, risk)
            candidates.append(
                SetupCandidate(
                    setup_type="previous_close_reclaim_long",
                    direction="LONG_CALL",
                    option_type="CE",
                    trigger_basis="close_above",
                    trigger_price=round(trigger_price, 2),
                    invalidation_level=round(previous_close - observation.atr * 0.25, 2),
                    defended_level=previous_close,
                    target_spot_price=target_spot,
                    first_target_price=round(current.close + risk, 2),
                    score=min(score, 100.0),
                    ready_to_enter=ready_to_enter,
                    notes=notes,
                    rule_ids=rule_ids,
                    event=SweepEvent(
                        side="sell",
                        level_label="Previous Day Close",
                        level_price=previous_close,
                        sweep_index=max(0, len(session) - len(recent_three)),
                        reclaim_index=len(session) - 1,
                        trigger_index=len(session) - 1 if ready_to_enter else None,
                        sweep_price=min(candle.low for candle in recent_three),
                        defended_level=previous_close,
                        trigger_price=round(trigger_price, 2),
                        invalidation_level=round(previous_close - observation.atr * 0.25, 2),
                        primary=True,
                        quality="tradable",
                        notes=notes,
                    ),
                )
            )

        if observation.previous_close_reclaim_short_ready:
            score = 58.0
            notes = ["Previous day close was revisited and rejected.", "Gap-fill or previous-close rejection is active."]
            rule_ids = ["R16", "R18", "R20", "R21", "R36", "R53", "R58", "R64", "R72", "R79", "R88", "R99"]
            if gap_up or gap_down:
                score += 10
                notes.append("Gap context makes the previous-close rejection more meaningful.")
            if observation.opening_confirmation in {"gap-up-trap-risk", "gap-down-confirmed", "flat-open"}:
                score += 6
                rule_ids.extend(["R16", "R18", "R83"])
            if observation.previous_day_bias.startswith("bearish"):
                score += 6
                notes.append("Previous session bias still supports bearish continuation or cleanup.")
                rule_ids.extend(["R15", "R81"])
            if observation.prior_close_psychology == "psychological-trap-close":
                score += 5
                notes.append("Prior close ended with crowd confusion, which supports a fresh rejection read.")
                rule_ids.append("R82")
            if observation.compression_day and observation.strong_intent:
                score += 6
                notes.append("Compressed prior session is expanding directionally today.")
                rule_ids.extend(["R96", "R97"])
            if recent_body_quality >= 0.55:
                score += 8
            if observation.strong_intent:
                score += 8
            if observation.value_state == "inflated":
                score += 6
            if observation.crowding_bias == "long-comfort":
                score += 5
                notes.append("Long-side comfort has built up, so downside trap fuel is available.")
                rule_ids.append("R102")
            trigger_reference = recent_three[:-1] if len(recent_three) > 1 else recent_three
            trigger_price = min(candle.low for candle in trigger_reference)
            ready_to_enter = current.close < trigger_price and current.close < current.open
            if ready_to_enter:
                score += 10
            target_spot = self.next_downside_target(context, current.close, risk)
            candidates.append(
                SetupCandidate(
                    setup_type="previous_close_rejection_short",
                    direction="LONG_PUT",
                    option_type="PE",
                    trigger_basis="close_below",
                    trigger_price=round(trigger_price, 2),
                    invalidation_level=round(previous_close + observation.atr * 0.25, 2),
                    defended_level=previous_close,
                    target_spot_price=target_spot,
                    first_target_price=round(current.close - risk, 2),
                    score=min(score, 100.0),
                    ready_to_enter=ready_to_enter,
                    notes=notes,
                    rule_ids=rule_ids,
                    event=SweepEvent(
                        side="buy",
                        level_label="Previous Day Close",
                        level_price=previous_close,
                        sweep_index=max(0, len(session) - len(recent_three)),
                        reclaim_index=len(session) - 1,
                        trigger_index=len(session) - 1 if ready_to_enter else None,
                        sweep_price=max(candle.high for candle in recent_three),
                        defended_level=previous_close,
                        trigger_price=round(trigger_price, 2),
                        invalidation_level=round(previous_close + observation.atr * 0.25, 2),
                        primary=True,
                        quality="tradable",
                        notes=notes,
                    ),
                )
            )
        return candidates

    def build_candidate_from_event(
        self,
        context: StrategyContext,
        observation: Observation,
        event: SweepEvent,
        *,
        option_type: str,
        direction: str,
    ) -> SetupCandidate | None:
        session = context.session_candles
        current = context.current_candle
        reclaim_index = event.reclaim_index
        if reclaim_index is None:
            return None

        reclaim_candle = session[reclaim_index]
        continuation_slice = session[reclaim_index + 1 : min(len(session), reclaim_index + 4)]
        current_strength = self.candle_strength(current)
        reclaim_strength = self.candle_strength(reclaim_candle)
        continuation_count = 0
        hold_count = 0
        follow_through = False

        for candle in continuation_slice:
            if option_type == "CE":
                if candle.close > event.defended_level:
                    hold_count += 1
                if candle.close > reclaim_candle.high:
                    continuation_count += 1
                    follow_through = True
            else:
                if candle.close < event.defended_level:
                    hold_count += 1
                if candle.close < reclaim_candle.low:
                    continuation_count += 1
                    follow_through = True

        score = 0.0
        notes = list(event.notes)
        rule_ids = ["R1", "R2", "R3", "R4", "R20", "R24", "R25", "R26", "R27", "R28", "R29", "R30", "R36", "R58"]
        if event.primary:
            score += 20
            notes.append("Primary liquidity was swept.")
            rule_ids.extend(["R59", "R77"])
        else:
            score += 12
        level_label = event.level_label.lower()
        if "round number" in level_label:
            score += 6
            notes.append("Sweep happened at a nearby round-number liquidity shelf.")
            rule_ids.extend(["R2", "R78", "R84"])
        if "equal high cluster" in level_label or "equal low cluster" in level_label:
            score += 8
            notes.append("Equal-high or equal-low stop cluster adds stronger trap potential.")
            rule_ids.extend(["R3", "R25", "R26", "R52", "R77"])
        if "same-day swing high" in level_label or "same-day swing low" in level_label:
            score += 5
            notes.append("Same-day swing liquidity map aligns with the sweep location.")
            rule_ids.extend(["R2", "R76", "R79"])
        same_side_levels = observation.mapped_sell_liquidity if option_type == "CE" else observation.mapped_buy_liquidity
        confluence_labels = [
            label.lower()
            for label, price, _ in same_side_levels
            if label != event.level_label and abs(price - event.level_price) <= max(observation.atr * 0.14, 0.3)
        ]
        if any("round number" in label for label in confluence_labels):
            score += 5
            notes.append("Round-number liquidity also overlaps the sweep price.")
            rule_ids.extend(["R2", "R78", "R84"])
        if any("equal high cluster" in label or "equal low cluster" in label for label in confluence_labels):
            score += 6
            notes.append("Clustered equal highs or lows reinforce stop concentration here.")
            rule_ids.extend(["R3", "R25", "R26", "R77"])
        if any("same-day swing high" in label or "same-day swing low" in label for label in confluence_labels):
            score += 4
            notes.append("Nearby same-day swing liquidity adds extra confluence.")
            rule_ids.extend(["R2", "R76", "R79"])
        if reclaim_index is not None:
            score += 15
        if "reclaimed back" in " ".join(event.notes).lower():
            rule_ids.extend(["R31", "R32"])
        if reclaim_strength >= 0.55:
            score += 10
            notes.append("Reclaim or rejection candle has healthy body quality.")
            rule_ids.append("R89")
        else:
            score -= 12
            notes.append("Reclaim or rejection candle is weak.")
        if follow_through:
            score += 12
            notes.append("Follow-through confirmed after the reclaim or rejection.")
            rule_ids.append("R89")
        else:
            score -= 15
            notes.append("Follow-through is still missing.")
        if hold_count >= 1:
            score += 8
            notes.append("Defended zone held after reclaim or rejection.")
            rule_ids.extend(["R33", "R34"])
        if observation.value_state == "discount" and option_type == "CE":
            score += 8
            rule_ids.append("R22")
        elif observation.value_state == "inflated" and option_type == "PE":
            score += 8
            rule_ids.append("R22")
        elif observation.value_state == "fair":
            score -= 12
            notes.append("Setup is still too close to fair value.")
            rule_ids.append("R21")
        if observation.value_state == "inflated" and option_type == "CE" and observation.day_type == "trend-day" and follow_through:
            score += 4
            notes.append("Bullish continuation is still acceptable because price is accepting above value.")
            rule_ids.extend(["R22", "R35"])
        if observation.value_state == "discount" and option_type == "PE" and observation.day_type == "trend-day" and follow_through:
            score += 4
            notes.append("Bearish continuation is still acceptable because price is accepting below value.")
            rule_ids.extend(["R22", "R35"])
        if observation.day_type in {"trap-day", "gap-reversal"}:
            score += 8
        if observation.day_type == "gap-and-go" and follow_through and option_type == "CE" and observation.opening_confirmation == "gap-up-confirmed":
            score += 8
            notes.append("Gap-and-go continuation is confirmed by the opening behavior.")
            rule_ids.extend(["R17", "R18", "R83"])
        if observation.day_type == "gap-and-go" and follow_through and option_type == "PE" and observation.opening_confirmation == "gap-down-confirmed":
            score += 8
            notes.append("Gap-and-go continuation is confirmed by the opening behavior.")
            rule_ids.extend(["R17", "R18", "R83"])
        if observation.day_type == "trend-day" and follow_through:
            score += 6
            rule_ids.extend(["R75", "R100"])
        if observation.day_type in {"range/sl-farming", "double-side-hunt"} and continuation_count == 0:
            score -= 10
            notes.append("Current day type still behaves like range or double-hunt.")
            rule_ids.extend(["R61", "R62", "R71", "R91"])
        if observation.large_gap_reset and observation.session_phase in {"discovery", "opening-map"} and continuation_count == 0:
            score -= 10
            notes.append("Large-gap reset day still needs more proof before trusting this setup.")
            rule_ids.extend(["R17", "R47", "R64", "R80"])
        if observation.session_phase in {"discovery", "opening-map"} and continuation_count == 0:
            score -= 10
            notes.append("Still too early to force confirmation from opening noise.")
            rule_ids.extend(["R60", "R64"])
        if observation.expiry_session and event.quality == "weak":
            score -= 8
            notes.append("Expiry-day conditions demand stronger confirmation than this weak trap.")
            rule_ids.append("R47")
        if current_strength < 0.35:
            score -= 12
            notes.append("Latest candle body is weak for entry.")
        if observation.weak_intent:
            score -= 8
            rule_ids.extend(["R92", "R94"])
        if observation.strong_intent and follow_through:
            score += 8
        if observation.two_sided_participation:
            score += 4
            notes.append("Both sides are participating, so SL-hunting logic is more believable here.")
            rule_ids.append("R68")
        if observation.stop_availability == "partially-cleared":
            score += 4
            notes.append("Stop availability is only partially cleared, so another directional push can still carry fuel.")
            rule_ids.extend(["R85", "R93"])
        elif observation.stop_availability == "cleared" and continuation_count == 0:
            score -= 5
            notes.append("That side already looks fully cleared, so fresh fuel may be reduced unless a new trap appears.")
            rule_ids.extend(["R54", "R93"])
        elif observation.stop_availability == "untouched":
            score -= 6
            notes.append("No meaningful stop clearance is visible yet.")
            rule_ids.extend(["R59", "R65", "R93"])
        if observation.operator_bias == "bullish" and option_type == "CE":
            score += 5
            notes.append("Nearby operator-demand behavior supports the bullish side.")
            rule_ids.extend(["R48", "R49"])
        if observation.operator_bias == "bearish" and option_type == "PE":
            score += 5
            notes.append("Nearby operator-supply behavior supports the bearish side.")
            rule_ids.extend(["R48", "R49"])
        if observation.crowding_bias == "long-comfort" and option_type == "PE":
            score += 6
            notes.append("Bullish comfort has become crowded, so the short-side trap has better asymmetry.")
            rule_ids.extend(["R23", "R102"])
        if observation.crowding_bias == "short-comfort" and option_type == "CE":
            score += 6
            notes.append("Bearish comfort has become crowded, so the long-side trap has better asymmetry.")
            rule_ids.extend(["R23", "R102"])
        if observation.crowding_bias == "long-comfort" and option_type == "CE" and observation.value_state == "inflated":
            score -= 6
            notes.append("This long setup risks chasing an already comfortable breakout crowd.")
            rule_ids.extend(["R23", "R51", "R101"])
        if observation.crowding_bias == "short-comfort" and option_type == "PE" and observation.value_state == "discount":
            score -= 6
            notes.append("This short setup risks chasing an already comfortable breakdown crowd.")
            rule_ids.extend(["R23", "R51", "R101"])
        if observation.previous_day_bias.startswith("bullish") and option_type == "CE":
            score += 4
            rule_ids.extend(["R15", "R81"])
        if observation.previous_day_bias.startswith("bearish") and option_type == "PE":
            score += 4
            rule_ids.extend(["R15", "R81"])
        slight_gap = abs(observation.gap) <= max(observation.atr * 0.45, 0.4)
        if observation.previous_day_bias.startswith("bullish") and slight_gap and observation.gap >= 0 and option_type == "CE" and observation.session_phase in {"opening-map", "primary-trap-window"}:
            score += 5
            notes.append("Small bullish gap with early liquidity dip still fits the bullish recovery template.")
            rule_ids.append("R67")
        if observation.prior_close_psychology == "psychological-trap-close":
            score += 3
            notes.append("Previous session closed with two-sided confusion, which often improves trap odds today.")
            rule_ids.append("R82")
        if observation.compression_day and follow_through:
            score += 5
            notes.append("Compressed prior day is releasing into a larger expansion move.")
            rule_ids.extend(["R96", "R97"])
        if observation.opening_confirmation == "gap-up-trap-risk" and option_type == "PE":
            score += 4
            rule_ids.extend(["R18", "R73", "R83"])
        if observation.opening_confirmation == "gap-down-trap-risk" and option_type == "CE":
            score += 4
            rule_ids.extend(["R18", "R73", "R83"])

        risk = max(abs(reclaim_candle.close - event.trigger_price), observation.atr * 0.8)
        if option_type == "CE":
            target_spot = self.next_upside_target(context, max(current.close, reclaim_candle.close), risk)
            first_target = current.close + risk
            retest_hold = hold_count >= 1 and current.close > event.defended_level and current.low <= reclaim_candle.high + observation.atr * 0.15
            ready_to_enter = (
                current.close > reclaim_candle.high and current.close > event.defended_level and current.close > current.open
            ) or (retest_hold and follow_through)
            trigger_basis = "close_above"
            trigger_price = max(reclaim_candle.high, event.defended_level + observation.atr * 0.1)
            invalidation = min(event.trigger_price, event.defended_level - observation.atr * 0.18)
        else:
            target_spot = self.next_downside_target(context, min(current.close, reclaim_candle.close), risk)
            first_target = current.close - risk
            retest_hold = hold_count >= 1 and current.close < event.defended_level and current.high >= reclaim_candle.low - observation.atr * 0.15
            ready_to_enter = (
                current.close < reclaim_candle.low and current.close < event.defended_level and current.close < current.open
            ) or (retest_hold and follow_through)
            trigger_basis = "close_below"
            trigger_price = min(reclaim_candle.low, event.defended_level - observation.atr * 0.1)
            invalidation = max(event.trigger_price, event.defended_level + observation.atr * 0.18)
        if ready_to_enter and hold_count >= 1 and continuation_count == 0:
            score += 4
            notes.append("Retest held after the break, so continuation entry is acceptable without chasing the breakout candle.")
            rule_ids.extend(["R63", "R100"])

        room = abs(target_spot - current.close)
        if room >= risk * 1.8:
            score += 10
            notes.append("There is enough room to the next opposing liquidity.")
            rule_ids.extend(["R44", "R98"])
        else:
            score -= 10
            notes.append("Reward-to-risk is weak to the next liquidity.")

        return SetupCandidate(
            setup_type="bullish_reclaim_watch" if option_type == "CE" else "bearish_rejection_watch",
            direction=direction,
            option_type=option_type,
            trigger_basis=trigger_basis,
            trigger_price=trigger_price,
            invalidation_level=invalidation,
            defended_level=event.defended_level,
            target_spot_price=target_spot,
            first_target_price=first_target,
            score=max(0.0, min(score, 100.0)),
            ready_to_enter=ready_to_enter,
            notes=notes,
            rule_ids=list(dict.fromkeys(rule_ids)),
            event=event,
        )

    def next_upside_target(self, context: StrategyContext, base_price: float, risk: float) -> float:
        upside_levels = sorted(
            zone.price
            for zone in context.liquidity_zones
            if zone.price > base_price + max(risk * 0.4, 0.2)
        )
        return round(upside_levels[0], 2) if upside_levels else round(base_price + max(risk * 2.2, 2.0), 2)

    def next_downside_target(self, context: StrategyContext, base_price: float, risk: float) -> float:
        downside_levels = sorted(
            (zone.price for zone in context.liquidity_zones if zone.price < base_price - max(risk * 0.4, 0.2)),
            reverse=True,
        )
        return round(downside_levels[0], 2) if downside_levels else round(base_price - max(risk * 2.2, 2.0), 2)

    def evaluate_pending_setup(
        self,
        context: StrategyContext,
        observation: Observation,
        candidates: list[SetupCandidate],
    ) -> TradeDecision | None:
        setup = context.pending_setup
        if setup is None:
            return None
        if getattr(setup, "status", "armed") != "armed":
            return None

        bars_open = sum(1 for candle in context.session_candles if candle.timestamp >= setup.created_at)
        current = context.current_candle
        best_same_side = next((candidate for candidate in candidates if candidate.option_type == setup.option_type), None)
        best_opposite = next((candidate for candidate in candidates if candidate.option_type != setup.option_type), None)
        moving_away_against_setup = False
        if setup.option_type == "CE":
            moving_away_against_setup = current.close < (setup.trigger_price - max(observation.atr * 0.6, 0.5))
        if setup.option_type == "PE":
            moving_away_against_setup = current.close > (setup.trigger_price + max(observation.atr * 0.6, 0.5))

        if bars_open > self.pending_setup_max_bars:
            return TradeDecision(
                action=TradeAction.no_trade,
                confidence=0.72,
                reason=f"Pending setup expired after {bars_open} candles without confirmation and must be cleared.",
                decision_source="heuristic",
                pending_setup_action="INVALIDATE",
                market_state=observation.day_type,
                rule_ids_used=["R37", "R38", "R39", "R60", "R87"],
            )

        if setup.invalidation_level is not None:
            if setup.option_type == "CE" and current.close < setup.invalidation_level:
                return TradeDecision(
                    action=TradeAction.no_trade,
                    confidence=0.74,
                    reason="Pending bullish setup invalidated because price closed back below its defended level.",
                    decision_source="heuristic",
                    pending_setup_action="INVALIDATE",
                    market_state=observation.day_type,
                    rule_ids_used=["R37", "R38", "R39", "R45", "R66", "R86"],
                )
            if setup.option_type == "PE" and current.close > setup.invalidation_level:
                return TradeDecision(
                    action=TradeAction.no_trade,
                    confidence=0.74,
                    reason="Pending bearish setup invalidated because price closed back above its defended level.",
                    decision_source="heuristic",
                    pending_setup_action="INVALIDATE",
                    market_state=observation.day_type,
                    rule_ids_used=["R37", "R38", "R39", "R45", "R66", "R86"],
                )

        if best_opposite and (
            (best_opposite.score >= self.enter_threshold and best_opposite.ready_to_enter)
            or (moving_away_against_setup and best_opposite.score >= self.arm_threshold)
        ):
            return TradeDecision(
                action=TradeAction.no_trade,
                confidence=min(0.88, best_opposite.score / 100),
                reason=(
                    "Opposite-side structure is now stronger than the armed setup, so replace the old idea. "
                    "The current setup is no longer aligned with price travel."
                ),
                decision_source="heuristic",
                market_state=observation.day_type,
                setup_score=best_opposite.score,
                setup_type=best_opposite.setup_type,
                pending_setup_action="REPLACE",
                pending_setup_type=best_opposite.setup_type,
                pending_setup_direction=best_opposite.direction,
                pending_setup_trigger_price=round(best_opposite.trigger_price, 2),
                pending_setup_invalidation_level=round(best_opposite.invalidation_level, 2),
                pending_setup_trigger_basis=best_opposite.trigger_basis,
                pending_setup_notes=self._candidate_reason(best_opposite, observation, enter_now=False),
                pending_setup_option_type=best_opposite.option_type,
                rule_ids_used=list(dict.fromkeys(best_opposite.rule_ids + ["R37", "R38", "R39", "R86"])),
            )

        if moving_away_against_setup and best_same_side is None:
            return TradeDecision(
                action=TradeAction.no_trade,
                confidence=0.76,
                reason="Price is moving away from the armed setup without same-side confirmation, so cancel it and remap.",
                decision_source="heuristic",
                pending_setup_action="INVALIDATE",
                market_state=observation.day_type,
                rule_ids_used=["R37", "R38", "R39", "R45", "R69", "R86", "R87"],
            )

        if best_same_side and abs(best_same_side.trigger_price - setup.trigger_price) <= max(observation.atr * 0.2, 0.5):
            return TradeDecision(
                action=TradeAction.no_trade,
                confidence=min(0.84, max(best_same_side.score, setup.confidence * 100) / 100),
                reason="Existing pending setup still matches the best same-side structure, so keep it locked.",
                decision_source="heuristic",
                market_state=observation.day_type,
                setup_score=best_same_side.score,
                setup_type=best_same_side.setup_type,
                pending_setup_action="KEEP",
                pending_setup_type=setup.setup_type,
                pending_setup_direction=setup.direction,
                pending_setup_trigger_price=setup.trigger_price,
                pending_setup_invalidation_level=setup.invalidation_level,
                pending_setup_trigger_basis=setup.trigger_basis,
                pending_setup_notes=self._candidate_reason(best_same_side, observation, enter_now=False),
                pending_setup_option_type=setup.option_type,
                rule_ids_used=list(dict.fromkeys(best_same_side.rule_ids + ["R37", "R38", "R39"])),
            )
        return None

    def manage_active_trade(
        self,
        context: StrategyContext,
        observation: Observation,
        current_trade_price: float | None,
    ) -> TradeDecision:
        trade = context.active_trade
        if trade is None:
            return TradeDecision(action=TradeAction.no_trade, confidence=0.0, reason="No active trade.")

        current_spot = context.current_candle.close
        current_price = current_trade_price if current_trade_price is not None else current_spot
        risk = max(abs(trade.entry_spot_price - (trade.invalidation_level or trade.entry_spot_price)), observation.atr * 0.6)
        bullish_trade = trade.direction in {"LONG_CALL", "LONG_STOCK"}
        progress_r = (
            (current_spot - trade.entry_spot_price) / max(risk, 0.01)
            if bullish_trade
            else (trade.entry_spot_price - current_spot) / max(risk, 0.01)
        )

        opposing_candidates = self.build_candidates(context, observation)
        strongest_opposite = next((candidate for candidate in opposing_candidates if (candidate.option_type == "PE") == bullish_trade), None)
        if strongest_opposite and strongest_opposite.ready_to_enter and strongest_opposite.score >= 78:
            return TradeDecision(
                action=TradeAction.exit,
                confidence=min(0.95, strongest_opposite.score / 100),
                reason="A strong opposite SL-hunting setup has confirmed against the active thesis.",
                decision_source="heuristic",
                option_type=trade.option_type,
                market_state=observation.day_type,
                setup_score=strongest_opposite.score,
                setup_type=strongest_opposite.setup_type,
                rule_ids_used=strongest_opposite.rule_ids + ["R45"],
            )

        if trade.invalidation_level is not None:
            if bullish_trade and current_spot < trade.invalidation_level:
                return TradeDecision(
                    action=TradeAction.exit,
                    confidence=0.9,
                    reason="Bullish defended zone was lost by clean close.",
                    decision_source="heuristic",
                    option_type=trade.option_type,
                    market_state=observation.day_type,
                    rule_ids_used=["R41", "R42", "R45", "R66"],
                )
            if (not bullish_trade) and current_spot > trade.invalidation_level:
                return TradeDecision(
                    action=TradeAction.exit,
                    confidence=0.9,
                    reason="Bearish defended zone was lost by clean close.",
                    decision_source="heuristic",
                    option_type=trade.option_type,
                    market_state=observation.day_type,
                    rule_ids_used=["R41", "R42", "R45", "R66"],
                )

        if trade.open_quantity and trade.open_quantity > 1 and trade.partial_exit_count == 0 and trade.first_target_price is not None:
            if bullish_trade and current_spot >= trade.first_target_price:
                return TradeDecision(
                    action=TradeAction.partial_exit,
                    confidence=0.82,
                    reason="First protection target reached, so book part and keep managing the remaining thesis.",
                    decision_source="heuristic",
                    option_type=trade.option_type,
                    partial_exit_quantity=max(1, trade.open_quantity // 2),
                    market_state=observation.day_type,
                    rule_ids_used=["R46", "R74", "R99"],
                )
            if (not bullish_trade) and current_spot <= trade.first_target_price:
                return TradeDecision(
                    action=TradeAction.partial_exit,
                    confidence=0.82,
                    reason="First protection target reached, so book part and keep managing the remaining thesis.",
                    decision_source="heuristic",
                    option_type=trade.option_type,
                    partial_exit_quantity=max(1, trade.open_quantity // 2),
                    market_state=observation.day_type,
                    rule_ids_used=["R46", "R74", "R99"],
                )

        if progress_r >= 1.0:
            latest_defense = self.latest_defended_zone(context, bullish_trade, observation)
            if bullish_trade:
                new_invalidation = max(trade.invalidation_level or trade.entry_spot_price, latest_defense, trade.entry_spot_price)
            else:
                new_invalidation = min(trade.invalidation_level or trade.entry_spot_price, latest_defense, trade.entry_spot_price)
            return TradeDecision(
                action=TradeAction.update_stop,
                confidence=0.76,
                reason="Trade has progressed beyond 1R and continuation is holding, so trail the defended zone.",
                decision_source="heuristic",
                option_type=trade.option_type,
                invalidation_level=round(new_invalidation, 2),
                market_state=observation.day_type,
                rule_ids_used=["R41", "R42", "R43", "R74", "R99"],
            )

        if progress_r >= 0.8 and trade.target_spot_price is not None:
            extended_target = self.extend_target_if_valid(context, observation, bullish_trade, trade.target_spot_price, risk)
            if extended_target is not None:
                return TradeDecision(
                    action=TradeAction.update_target,
                    confidence=0.68,
                    reason="Continuation acceptance keeps room open to the next liquidity pool, so extend target.",
                    decision_source="heuristic",
                    option_type=trade.option_type,
                    target_spot_price=round(extended_target, 2),
                    market_state=observation.day_type,
                    rule_ids_used=["R41", "R42", "R44", "R75", "R100"],
                )

        return TradeDecision(
            action=TradeAction.hold,
            confidence=0.58,
            reason=(
                f"Hold the open trade. Session is classified as {observation.day_type}, "
                f"current progress is about {progress_r:.2f}R, and no clean invalidation is present yet."
            ),
            decision_source="heuristic",
            option_type=trade.option_type,
            market_state=observation.day_type,
            rule_ids_used=["R41", "R42", "R74", "R90", "R99"],
        )

    def latest_defended_zone(self, context: StrategyContext, bullish_trade: bool, observation: Observation) -> float:
        recent = context.recent_candles[-4:] or [context.current_candle]
        if bullish_trade:
            return round(min(candle.low for candle in recent) - max(observation.atr * 0.1, 0.1), 2)
        return round(max(candle.high for candle in recent) + max(observation.atr * 0.1, 0.1), 2)

    def extend_target_if_valid(
        self,
        context: StrategyContext,
        observation: Observation,
        bullish_trade: bool,
        current_target: float,
        risk: float,
    ) -> float | None:
        if observation.day_type not in {"trend-day", "gap-and-go", "trap-day"}:
            return None
        if bullish_trade:
            next_target = self.next_upside_target(context, max(context.current_candle.close, current_target), risk)
            return next_target if next_target > current_target + risk * 0.4 else None
        next_target = self.next_downside_target(context, min(context.current_candle.close, current_target), risk)
        return next_target if next_target < current_target - risk * 0.4 else None

    def select_best_candidate(self, candidates: list[SetupCandidate]) -> SetupCandidate | None:
        if not candidates:
            return None
        return max(candidates, key=lambda item: item.score)

    def _candidate_reason(self, candidate: SetupCandidate, observation: Observation, *, enter_now: bool) -> str:
        action_phrase = "Entry is allowed now" if enter_now else "Setup should stay armed"
        joined_notes = " ".join(candidate.notes[:4])
        return (
            f"{action_phrase} because {observation.day_type} conditions show a {candidate.setup_type} with "
            f"score {candidate.score:.1f}/100. {joined_notes}"
        )

    def record_trace(
        self,
        context: StrategyContext,
        observation: Observation,
        decision: TradeDecision,
        candidates: list[SetupCandidate] | None = None,
    ) -> None:
        candidates = candidates if candidates is not None else self.build_candidates(context, observation)
        best = self.select_best_candidate(candidates)
        setup_type = decision.setup_type or decision.pending_setup_type or (best.setup_type if best is not None else None)
        option_type = decision.option_type or decision.pending_setup_option_type or (best.option_type if best is not None else None)
        setup_score = decision.setup_score if decision.setup_score is not None else (best.score if best is not None else None)
        trigger_price = decision.pending_setup_trigger_price
        if trigger_price is None and best is not None:
            trigger_price = round(best.trigger_price, 2)
        invalidation_level = decision.pending_setup_invalidation_level
        if invalidation_level is None and decision.invalidation_level is not None:
            invalidation_level = decision.invalidation_level
        if invalidation_level is None and best is not None:
            invalidation_level = round(best.invalidation_level, 2)

        status = "informational"
        block_reason = None
        if decision.action in {TradeAction.enter_call, TradeAction.enter_put}:
            status = "trade_entered"
        elif decision.action in {TradeAction.exit, TradeAction.partial_exit, TradeAction.update_stop, TradeAction.update_target, TradeAction.hold}:
            status = "active_trade_management"
        elif decision.pending_setup_action == "ARM":
            status = "setup_armed"
        elif decision.pending_setup_action == "REPLACE":
            status = "setup_replaced"
        elif decision.pending_setup_action == "KEEP":
            status = "pending_not_triggered"
        elif decision.pending_setup_action == "INVALIDATE":
            status = "setup_invalidated"
        elif context.active_trade is not None:
            status = "blocked_active_trade"
            block_reason = "Existing trade was open, so heuristic stayed in trade-management mode."
        elif best is None:
            status = "no_setup_identified"
            block_reason = "No setup family reached minimum structural quality."
        elif best.score < self.arm_threshold:
            status = "failed_threshold"
            block_reason = (
                f"Best setup score {best.score:.1f} stayed below arm threshold {self.arm_threshold:.1f}."
            )
        elif not best.ready_to_enter:
            status = "setup_identified_waiting_confirmation"
            block_reason = "Setup was identified but confirmation or trigger basis was not satisfied yet."
        else:
            status = "setup_identified_waiting_confirmation"
            block_reason = "Setup was tradable structurally but still below direct-entry confidence."

        is_important = (
            decision.pending_setup_action != "NONE"
            or decision.action in {TradeAction.enter_call, TradeAction.enter_put, TradeAction.exit, TradeAction.partial_exit, TradeAction.update_stop, TradeAction.update_target}
            or best is not None
            or context.active_trade is not None
            or bool(observation.buy_sweeps or observation.sell_sweeps or observation.previous_close_touched)
        )
        if not is_important:
            return

        title = f"{status.replace('_', ' ').title()} on {context.current_candle.timestamp.strftime('%H:%M')}"
        if setup_type:
            title = f"{setup_type} | {status.replace('_', ' ')}"
        detail = decision.reason
        if block_reason and block_reason not in detail:
            detail = f"{detail} {block_reason}".strip()
        if status == "active_trade_management" and decision.action == TradeAction.hold and self._trace_entries:
            last_entry = self._trace_entries[-1]
            same_hold_status = (
                last_entry.get("status") == "active_trade_management"
                and last_entry.get("action") == TradeAction.hold.value
                and last_entry.get("setup_type") == setup_type
                and last_entry.get("market_state") == (decision.market_state or observation.day_type)
            )
            recent_enough = (context.current_candle.timestamp - last_entry["timestamp"]) < timedelta(minutes=5)
            prior_score = last_entry.get("setup_score")
            prior_trigger = last_entry.get("trigger_price")
            prior_invalidation = last_entry.get("invalidation_level")
            similar_metrics = (
                (prior_score is None or setup_score is None or abs(prior_score - setup_score) <= 5)
                and (prior_trigger is None or trigger_price is None or abs(prior_trigger - trigger_price) <= 0.25)
                and (prior_invalidation is None or invalidation_level is None or abs(prior_invalidation - invalidation_level) <= 0.25)
            )
            if same_hold_status and recent_enough and similar_metrics:
                return
        self._trace_entries.append(
            {
                "timestamp": context.current_candle.timestamp,
                "event_type": "decision-audit",
                "title": title,
                "status": status,
                "market_state": decision.market_state or observation.day_type,
                "action": decision.action.value,
                "direction": decision.pending_setup_direction or (best.direction if best is not None else None),
                "setup_type": setup_type,
                "option_type": option_type,
                "confidence": decision.confidence,
                "setup_score": setup_score,
                "trigger_price": trigger_price,
                "invalidation_level": invalidation_level,
                "block_reason": block_reason,
                "detail": detail,
            }
        )

    def record_narrative(self, context: StrategyContext, observation: Observation, decision: TradeDecision) -> None:
        current = context.current_candle
        for event in observation.buy_sweeps[:2] + observation.sell_sweeps[:2]:
            if event.reclaim_index == len(context.session_candles) - 1:
                title = f"{event.level_label} {event.quality} {'buyer' if event.side == 'buy' else 'seller'} trap"
                detail = " ".join(event.notes[:3])
                self._push_narrative(
                    timestamp=current.timestamp,
                    event_type="major-sweep",
                    title=title,
                    direction="LONG_PUT" if event.side == "buy" else "LONG_CALL",
                    price=event.level_price,
                    status=event.quality,
                    detail=detail,
                )

        if observation.previous_close_touched and (
            observation.previous_close_reclaim_long_ready or observation.previous_close_reclaim_short_ready
        ):
            self._push_narrative(
                timestamp=current.timestamp,
                event_type="gap-fill",
                title="Previous day close gap-fill interaction",
                direction="LONG_CALL" if observation.previous_close_reclaim_long_ready else "LONG_PUT",
                price=context.previous_day.close,
                status="defended",
                detail="Previous-day close was revisited and is now acting as a live reversal or defense reference.",
            )

        if decision.pending_setup_action in {"ARM", "REPLACE", "INVALIDATE", "KEEP"}:
            self._push_narrative(
                timestamp=current.timestamp,
                event_type="pending-setup",
                title=f"Pending setup {decision.pending_setup_action.lower()}",
                direction=decision.pending_setup_direction,
                price=decision.pending_setup_trigger_price,
                status=decision.pending_setup_action.lower(),
                detail=decision.reason,
            )

        if decision.action in {TradeAction.enter_call, TradeAction.enter_put, TradeAction.partial_exit, TradeAction.exit}:
            self._push_narrative(
                timestamp=current.timestamp,
                event_type="trade-action",
                title=f"Trade action {decision.action.value}",
                direction="LONG_CALL" if decision.action == TradeAction.enter_call else "LONG_PUT" if decision.action == TradeAction.enter_put else None,
                price=context.current_candle.close,
                status=decision.action.value.lower(),
                detail=decision.reason,
            )

    def _push_narrative(
        self,
        *,
        timestamp,
        event_type: str,
        title: str,
        direction: str | None,
        price: float | None,
        status: str | None,
        detail: str,
    ) -> None:
        if event_type in {"gap-fill", "major-sweep"}:
            key = (
                event_type,
                title,
                status or "",
                f"{price:.2f}" if isinstance(price, (int, float)) else "",
                detail.strip(),
            )
        else:
            key = (timestamp.isoformat(), event_type, title)
        if key in self._narrative_keys:
            return
        self._narrative_keys.add(key)
        self._narrative_events.append(
            {
                "timestamp": timestamp,
                "event_type": event_type,
                "title": title,
                "direction": direction,
                "price": price,
                "status": status,
                "detail": detail,
            }
        )

    def _overlap_ratio(self, candles: list[Candle]) -> float:
        if len(candles) < 3:
            return 0.0
        overlaps = 0
        comparisons = 0
        for previous, current in zip(candles, candles[1:]):
            previous_range = max(previous.high - previous.low, 0.01)
            overlap = max(0.0, min(previous.high, current.high) - max(previous.low, current.low))
            overlaps += 1 if overlap / previous_range >= 0.45 else 0
            comparisons += 1
        return overlaps / max(comparisons, 1)
