from __future__ import annotations

from datetime import date, time as dt_time, timedelta
from dataclasses import dataclass, field
import math
from statistics import median

from app.schemas import Candle, PendingSetup, SimulatedTrade, StrategyContext, TradeAction, TradeDecision


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
    range_state: str
    participation_state: str
    regime_quality: float
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
    higher_timeframe_context: str = "neutral"
    nifty_mid_noise: bool = False
    stock_dow_bias: str = "neutral"
    stock_dow_state: str = "mixed"
    stock_nifty_bias: str = "neutral"
    stock_nifty_state: str = "unavailable"
    layered_bullish_trap_score: float = 0.0
    layered_bearish_trap_score: float = 0.0
    liquidity_ledger_summary: str = ""


class HeuristicDecisionEngine:
    def __init__(self) -> None:
        self.enter_threshold = 68.0
        self.arm_threshold = 52.0
        self.pending_setup_max_bars = 10
        self.previous_day_structure_window = 180
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
        self.record_narrative(context, observation, decision, candidates)
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
            recent_interaction_window = session[-min(8, len(session)) :]
            previous_close_touched = any(
                candle.low <= previous.close + tolerance and candle.high >= previous.close - tolerance
                for candle in recent_interaction_window
            )
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
        range_state = self.classify_range_state(session, atr, overlap_ratio)
        participation_state = self.classify_participation_state(session, previous.close, vwap, atr)
        regime_quality = self.regime_quality_score(range_state, participation_state)
        previous_day_bias = self.classify_previous_day_bias(context.previous_day_candles)
        prior_close_psychology = self.classify_prior_close_psychology(context.previous_day_candles)
        opening_confirmation = self.classify_opening_confirmation(session, gap, atr)
        expiry_session = current.timestamp.weekday() == 3
        large_gap_reset = abs(gap) > atr * 1.1
        compression_day = self.is_compression_day(context.previous_day_candles, atr)
        two_sided_participation = self.detect_two_sided_participation(session)
        operator_bias = self.classify_operator_bias(context.operator_zones, current.close, atr)
        crowding_bias = self.classify_crowding_bias(session, current, atr, value_state)
        mapped_buy_liquidity, mapped_sell_liquidity = self.build_liquidity_maps(
            session,
            context.previous_day_candles,
            previous,
            current.close,
            atr,
        )
        allowed_liquidity_families = self._allowed_liquidity_families_for_context(context)
        mapped_buy_liquidity = self._filter_liquidity_levels(mapped_buy_liquidity, allowed_liquidity_families)
        mapped_sell_liquidity = self._filter_liquidity_levels(mapped_sell_liquidity, allowed_liquidity_families)

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
            allowed_families=allowed_liquidity_families,
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
            allowed_families=allowed_liquidity_families,
        )
        stop_availability = self.assess_stop_availability(buy_sweeps, sell_sweeps)
        higher_timeframe_context = self.higher_timeframe_context(context, atr)
        nifty_mid_noise = self.is_nifty_mid_noise(context, atr, overlap_ratio, mapped_buy_liquidity, mapped_sell_liquidity)
        stock_dow_bias, stock_dow_state = self._classify_stock_dow_structure(session, atr)
        stock_nifty_bias, stock_nifty_state = self._classify_stock_nifty_context(context, atr)
        layered_bullish_trap_score, layered_bearish_trap_score, liquidity_ledger_summary = self._liquidity_ledger_scores(context)

        return Observation(
            session_phase=session_phase,
            day_type=day_type,
            value_state=value_state,
            range_state=range_state,
            participation_state=participation_state,
            regime_quality=regime_quality,
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
            higher_timeframe_context=higher_timeframe_context,
            nifty_mid_noise=nifty_mid_noise,
            stock_dow_bias=stock_dow_bias,
            stock_dow_state=stock_dow_state,
            stock_nifty_bias=stock_nifty_bias,
            stock_nifty_state=stock_nifty_state,
            layered_bullish_trap_score=layered_bullish_trap_score,
            layered_bearish_trap_score=layered_bearish_trap_score,
            liquidity_ledger_summary=liquidity_ledger_summary,
        )

    def _liquidity_ledger_scores(self, context: StrategyContext) -> tuple[float, float, str]:
        bullish = 0.0
        bearish = 0.0
        important: list[str] = []
        status_weight = {"reclaimed": 1.0, "swept": 0.42, "accepted": 0.58}
        for entry in context.liquidity_ledger:
            weight = status_weight.get(entry.status, 0.0)
            if weight <= 0.0:
                continue
            strength_points = entry.strength * weight * 10.0
            if entry.side == "sell-side":
                if entry.status == "reclaimed":
                    bullish += strength_points
                elif entry.status == "accepted":
                    bearish += strength_points * 0.65
                elif entry.status == "swept":
                    bullish += strength_points * 0.45
            elif entry.side == "buy-side":
                if entry.status == "reclaimed":
                    bearish += strength_points
                elif entry.status == "accepted":
                    bullish += strength_points * 0.65
                elif entry.status == "swept":
                    bearish += strength_points * 0.45
            if len(important) < 4:
                important.append(f"{entry.window_label} {entry.side} {entry.status}")
        return min(bullish, 14.0), min(bearish, 14.0), "; ".join(important)

    def _liquidity_ledger_score_adjustment(
        self,
        observation: Observation,
        *,
        option_type: str,
    ) -> tuple[float, str | None, list[str]]:
        same_score = observation.layered_bullish_trap_score if option_type == "CE" else observation.layered_bearish_trap_score
        opposite_score = observation.layered_bearish_trap_score if option_type == "CE" else observation.layered_bullish_trap_score
        adjustment = min(same_score, 10.0) - min(opposite_score * 0.45, 6.0)
        if adjustment >= 3.0:
            side_text = "bullish" if option_type == "CE" else "bearish"
            return (
                adjustment,
                f"Multi-window liquidity memory supports the {side_text} trap/reclaim context: {observation.liquidity_ledger_summary}.",
                ["R77", "R78", "R79", "R93", "R94"],
            )
        if adjustment <= -3.0:
            side_text = "bullish" if option_type == "CE" else "bearish"
            return (
                adjustment,
                f"Multi-window liquidity memory warns against the {side_text} side because opposite trap pressure is active.",
                ["R50", "R77", "R93", "R94"],
            )
        return adjustment, None, []

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

    def classify_range_state(self, session: list[Candle], atr: float, overlap_ratio: float) -> str:
        if len(session) < 10:
            return "balanced"
        window = min(10, max(8, len(session) // 3))
        recent = session[-window:]
        prior = session[-window * 2 : -window]
        if len(prior) < max(6, window - 2):
            return "balanced"

        recent_range = max(candle.high for candle in recent) - min(candle.low for candle in recent)
        prior_range = max(candle.high for candle in prior) - min(candle.low for candle in prior)
        recent_overlap = self._overlap_ratio(recent)
        recent_high_extension = max(candle.high for candle in recent) > max(candle.high for candle in prior) + max(atr * 0.1, 0.1)
        recent_low_extension = min(candle.low for candle in recent) < min(candle.low for candle in prior) - max(atr * 0.1, 0.1)
        extension_count = int(recent_high_extension) + int(recent_low_extension)

        if recent_range >= prior_range * 1.12 and recent_overlap <= 0.48 and extension_count >= 1:
            return "expanding"
        if recent_range <= prior_range * 0.92 and overlap_ratio >= 0.58 and recent_overlap >= 0.55 and extension_count == 0:
            return "compressing"
        return "balanced"

    def classify_participation_state(self, session: list[Candle], previous_close: float, vwap: float, atr: float) -> str:
        if len(session) < 10:
            return "two_sided_active"
        window = min(10, max(8, len(session) // 3))
        recent = session[-window:]
        prior = session[-window * 2 : -window]
        anchor_tolerance = max(atr * 0.18, 0.2)
        anchors = [vwap]
        if previous_close:
            anchors.append(previous_close)
        closes_near_anchor = sum(
            1
            for candle in recent
            if any(abs(candle.close - anchor) <= anchor_tolerance for anchor in anchors)
        )
        up_closes = sum(1 for candle in recent if candle.close > candle.open)
        down_closes = sum(1 for candle in recent if candle.close < candle.open)
        recent_displacement = abs(recent[-1].close - recent[0].open)
        recent_overlap = self._overlap_ratio(recent)
        recent_range = max(candle.high for candle in recent) - min(candle.low for candle in recent)

        if closes_near_anchor >= max(4, window // 2) and recent_displacement <= atr * 1.1 and up_closes >= 3 and down_closes >= 3:
            return "fair_value_churn"

        if prior:
            prior_range = max(candle.high for candle in prior) - min(candle.low for candle in prior)
            prior_displacement = abs(prior[-1].close - prior[0].open)
            if (
                prior_displacement >= atr * 1.8
                and recent_range <= max(prior_range * 0.72, atr * 0.9)
                and recent_displacement <= atr
                and recent_overlap >= 0.54
            ):
                return "post_trend_balance"

        return "two_sided_active"

    def regime_quality_score(self, range_state: str, participation_state: str) -> float:
        range_scores = {
            "expanding": 8.0,
            "balanced": 0.0,
            "compressing": -8.0,
        }
        participation_scores = {
            "two_sided_active": 4.0,
            "fair_value_churn": -10.0,
            "post_trend_balance": -8.0,
        }
        return range_scores.get(range_state, 0.0) + participation_scores.get(participation_state, 0.0)

    def apply_regime_filter(
        self,
        score: float,
        notes: list[str],
        rule_ids: list[str],
        observation: Observation,
    ) -> float:
        if observation.range_state == "expanding":
            notes.append("Recent range is expanding cleanly, so fresh follow-through is more believable here.")
            rule_ids.extend(["R58", "R75", "R92", "R95"])
        elif observation.range_state == "compressing":
            notes.append("Recent range is compressing, so breakout follow-through risk is higher here.")
            rule_ids.extend(["R55", "R60", "R91", "R95"])

        if observation.participation_state == "fair_value_churn":
            notes.append("Price is rotating near value, so fair-value churn reduces the edge of this setup.")
            rule_ids.extend(["R21", "R50", "R55", "R91"])
        elif observation.participation_state == "post_trend_balance":
            notes.append("The earlier move has cooled into post-trend balance, so continuation trust is reduced.")
            rule_ids.extend(["R54", "R55", "R74", "R90", "R91"])
        elif observation.participation_state == "two_sided_active":
            notes.append("Two-sided active participation supports a cleaner SL-hunting move.")
            rule_ids.extend(["R58", "R68", "R77", "R78"])

        return score + observation.regime_quality

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
        previous_day_candles: list[Candle],
        previous_day,
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

        for label, price, primary in self._pivot_liquidity_levels(previous_day):
            target_levels = buy_levels if label in {"Pivot Point", "Pivot R1", "Pivot R2"} else sell_levels
            self._append_liquidity_level(
                target_levels,
                label=label,
                price=price,
                primary=primary,
                tolerance=max(tolerance, atr * 0.18),
            )

        previous_day_window = previous_day_candles[-self.previous_day_structure_window :]
        previous_day_ranges = [max(candle.high - candle.low, 0.01) for candle in previous_day_window] or [atr]
        previous_day_atr = median(previous_day_ranges)
        prev_tolerance = max(previous_day_atr * 0.12, 0.2)
        prev_swing_highs, prev_swing_lows = self._detect_session_swings(previous_day_candles, previous_day_atr)
        for label, price in prev_swing_highs[-3:]:
            self._append_liquidity_level(
                buy_levels,
                label=f"Previous-Day Swing High {label}",
                price=price,
                primary=False,
                tolerance=prev_tolerance,
            )
        for label, price in prev_swing_lows[-3:]:
            self._append_liquidity_level(
                sell_levels,
                label=f"Previous-Day Swing Low {label}",
                price=price,
                primary=False,
                tolerance=prev_tolerance,
            )

        prev_high_clusters = sorted(
            self._cluster_price_levels([candle.high for candle in previous_day_candles], prev_tolerance),
            key=lambda item: (item[1], item[0]),
            reverse=True,
        )
        prev_low_clusters = sorted(
            self._cluster_price_levels([candle.low for candle in previous_day_candles], prev_tolerance),
            key=lambda item: (item[1], -item[0]),
        )
        for cluster_price, touches in prev_high_clusters[:2]:
            self._append_liquidity_level(
                buy_levels,
                label=f"Previous-Day Resistance Shelf ({touches} touches)",
                price=cluster_price,
                primary=True,
                tolerance=prev_tolerance,
            )
        for cluster_price, touches in prev_low_clusters[:2]:
            self._append_liquidity_level(
                sell_levels,
                label=f"Previous-Day Support Shelf ({touches} touches)",
                price=cluster_price,
                primary=True,
                tolerance=prev_tolerance,
            )

        return buy_levels[:12], sell_levels[:12]

    def _pivot_liquidity_levels(self, previous_day) -> list[tuple[str, float, bool]]:
        if not previous_day.high or not previous_day.low or not previous_day.close:
            return []
        pivot = (previous_day.high + previous_day.low + previous_day.close) / 3
        day_range = previous_day.high - previous_day.low
        r1 = (2 * pivot) - previous_day.low
        s1 = (2 * pivot) - previous_day.high
        r2 = pivot + day_range
        s2 = pivot - day_range
        return [
            ("Pivot Point", round(pivot, 2), True),
            ("Pivot R1", round(r1, 2), True),
            ("Pivot R2", round(r2, 2), False),
            ("Pivot S1", round(s1, 2), True),
            ("Pivot S2", round(s2, 2), False),
        ]

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
        label_family = self._label_family(label)
        for existing_label, existing_price, _ in levels:
            same_family = self._label_family(existing_label) == label_family
            if existing_label == label or (same_family and abs(existing_price - price) <= tolerance):
                return
        levels.append((label, round(price, 2), primary))

    def _label_family(self, label: str) -> str:
        lowered = label.lower()
        families = (
            "round number",
            "equal high cluster",
            "equal low cluster",
            "same-day swing high",
            "same-day swing low",
            "previous-day swing high",
            "previous-day swing low",
            "previous-day resistance shelf",
            "previous-day support shelf",
            "pivot point",
            "pivot r1",
            "pivot r2",
            "pivot s1",
            "pivot s2",
            "previous day high",
            "previous day low",
            "opening range high",
            "opening range low",
            "first 15m high",
            "first 15m low",
            "prior hour high",
            "prior hour low",
            "session extreme",
        )
        for family in families:
            if lowered.startswith(family):
                return family
        parts = label.split()
        return " ".join(parts[:2]).lower() if len(parts) >= 2 else lowered

    def _round_number_step(self, reference_price: float) -> float:
        absolute = abs(reference_price)
        if absolute >= 500:
            return 50.0
        if absolute >= 100:
            return 10.0
        return 5.0

    def _is_nifty_mode(self, context: StrategyContext) -> bool:
        return context.instrument.symbol == "NIFTY" and context.instrument.supports_options

    def _aggregate_session_candles(self, session: list[Candle], timeframe_minutes: int) -> list[Candle]:
        if not session:
            return []
        anchor = session[0].timestamp
        buckets: list[Candle] = []
        current_bucket: Candle | None = None
        current_bucket_start = anchor
        for candle in session:
            minutes_from_anchor = int((candle.timestamp - anchor).total_seconds() // 60)
            bucket_start = anchor + timedelta(minutes=(minutes_from_anchor // timeframe_minutes) * timeframe_minutes)
            if current_bucket is None or bucket_start != current_bucket_start:
                if current_bucket is not None:
                    buckets.append(current_bucket)
                current_bucket_start = bucket_start
                current_bucket = Candle(
                    timestamp=bucket_start,
                    open=candle.open,
                    high=candle.high,
                    low=candle.low,
                    close=candle.close,
                    volume=candle.volume,
                )
                continue
            current_bucket.high = max(current_bucket.high, candle.high)
            current_bucket.low = min(current_bucket.low, candle.low)
            current_bucket.close = candle.close
            current_bucket.volume += candle.volume
        if current_bucket is not None:
            buckets.append(current_bucket)
        return buckets

    def higher_timeframe_context(self, context: StrategyContext, atr: float) -> str:
        if not self._is_nifty_mode(context):
            return "neutral"
        fifteen = self._aggregate_session_candles(context.session_candles, 15)
        thirty = self._aggregate_session_candles(context.session_candles, 30)
        bias_15 = self._classify_higher_timeframe_bias(fifteen, atr)
        bias_30 = self._classify_higher_timeframe_bias(thirty, atr)
        if bias_30.endswith("trend") and bias_30 == bias_15:
            return bias_30
        if bias_15 != "neutral":
            return bias_15
        return bias_30

    def _classify_higher_timeframe_bias(self, candles: list[Candle], atr: float) -> str:
        if len(candles) < 2:
            return "neutral"
        recent = candles[-3:]
        last = recent[-1]
        previous = recent[-2]
        ranges = [max(candle.high - candle.low, 0.01) for candle in recent]
        htf_atr = median(ranges) if ranges else max(atr, 1.0)
        displacement = last.close - recent[0].open
        bullish_structure = last.high >= previous.high and last.low >= previous.low - htf_atr * 0.15
        bearish_structure = last.low <= previous.low and last.high <= previous.high + htf_atr * 0.15
        if bullish_structure and displacement > htf_atr * 0.85 and last.close > previous.close:
            return "bullish_trend"
        if bearish_structure and displacement < -htf_atr * 0.85 and last.close < previous.close:
            return "bearish_trend"
        if (
            previous.close < previous.open
            and last.close > last.open
            and last.close > previous.high
            and displacement > htf_atr * 0.45
        ):
            return "bullish_reversal"
        if (
            previous.close > previous.open
            and last.close < last.open
            and last.close < previous.low
            and displacement < -htf_atr * 0.45
        ):
            return "bearish_reversal"
        return "range"

    def is_nifty_mid_noise(
        self,
        context: StrategyContext,
        atr: float,
        overlap_ratio: float,
        mapped_buy_liquidity: list[tuple[str, float, bool]],
        mapped_sell_liquidity: list[tuple[str, float, bool]],
    ) -> bool:
        if not self._is_nifty_mode(context):
            return False
        current_close = context.current_candle.close
        lower_levels = [price for _, price, _ in mapped_sell_liquidity if price < current_close]
        upper_levels = [price for _, price, _ in mapped_buy_liquidity if price > current_close]
        if not lower_levels or not upper_levels:
            return False
        nearest_lower = max(lower_levels)
        nearest_upper = min(upper_levels)
        pocket_width = nearest_upper - nearest_lower
        low_atr = atr <= max(self._round_number_step(current_close) * 0.36, 18.0)
        pinned_between_liquidity = (
            current_close - nearest_lower <= max(atr * 0.85, 12.0)
            and nearest_upper - current_close <= max(atr * 0.85, 12.0)
            and pocket_width <= max(atr * 1.7, 26.0)
        )
        return low_atr and overlap_ratio >= 0.7 and pinned_between_liquidity

    def _nifty_higher_timeframe_allows(self, context: StrategyContext, observation: Observation, option_type: str) -> bool:
        if not self._is_nifty_mode(context):
            return True
        htf = observation.higher_timeframe_context
        if option_type == "CE":
            return htf not in {"bearish_trend"}
        return htf not in {"bullish_trend"}

    def _nifty_immediate_sweep_reclaim(
        self,
        context: StrategyContext,
        event: SweepEvent,
        observation: Observation,
        *,
        option_type: str,
        current: Candle,
        reclaim_strength: float,
    ) -> bool:
        if not self._is_nifty_mode(context) or not event.primary or observation.weak_intent:
            return False
        if event.reclaim_index != len(context.session_candles) - 1:
            return False
        if reclaim_strength < 0.05:
            return False
        if option_type == "CE":
            return current.close > event.defended_level and current.close > current.open
        return current.close < event.defended_level and current.close < current.open

    def _enforce_nifty_three_r_target(
        self,
        context: StrategyContext,
        *,
        option_type: str,
        entry_price: float,
        target_spot: float,
        risk: float,
    ) -> tuple[float, bool]:
        if not self._is_nifty_mode(context):
            return target_spot, False
        plan_risk = max(abs(risk), 0.01)
        minimum_distance = plan_risk * 3.0
        max_reasonable_distance = max(plan_risk * 8.0, 350.0)
        if option_type == "CE":
            minimum_target = entry_price + minimum_distance
            adjusted = max(target_spot, minimum_target)
            if adjusted - entry_price > max_reasonable_distance:
                adjusted = minimum_target
        else:
            minimum_target = entry_price - minimum_distance
            adjusted = min(target_spot, minimum_target)
            if entry_price - adjusted > max_reasonable_distance:
                adjusted = minimum_target
        return round(adjusted, 2), abs(adjusted - target_spot) >= 0.01

    def _stock_early_retest_entry(
        self,
        context: StrategyContext,
        observation: Observation,
        event: SweepEvent,
        *,
        option_type: str,
        setup_type: str,
        current: Candle,
        reclaim_candle: Candle,
        current_strength: float,
        reclaim_strength: float,
        hold_count: int,
        continuation_count: int,
    ) -> bool:
        if context.instrument.supports_options:
            return False
        if not event.primary or setup_type not in {"bullish_reclaim_watch", "bearish_rejection_watch"}:
            return False
        if not self._stock_dow_trend_allows(observation, option_type=option_type, strict=True):
            return False
        if event.reclaim_index is None or len(context.session_candles) <= event.reclaim_index + 1:
            return False
        bars_after_reclaim = len(context.session_candles) - event.reclaim_index - 1
        if bars_after_reclaim > 3:
            return False
        if hold_count < 1 or observation.weak_intent:
            return False
        if continuation_count > 1 and bars_after_reclaim > 2:
            return False
        if current_strength < 0.2 or reclaim_strength < 0.38:
            return False
        if option_type == "CE":
            shallow_retest = (
                current.low >= event.defended_level - observation.atr * 0.14
                and current.low <= reclaim_candle.high + observation.atr * 0.12
            )
            near_session_low = abs(event.sweep_price - observation.session_low) <= max(observation.atr * 0.28, 0.2)
            return (
                shallow_retest
                and near_session_low
                and current.close > event.defended_level
                and current.close > current.open
                and current.close >= reclaim_candle.close - observation.atr * 0.08
            )
        shallow_retest = (
            current.high <= event.defended_level + observation.atr * 0.14
            and current.high >= reclaim_candle.low - observation.atr * 0.12
        )
        near_session_high = abs(event.sweep_price - observation.session_high) <= max(observation.atr * 0.28, 0.2)
        return (
            shallow_retest
            and near_session_high
            and current.close < event.defended_level
            and current.close < current.open
            and current.close <= reclaim_candle.close + observation.atr * 0.08
        )

    def _recent_profitable_stock_trade(
        self,
        context: StrategyContext,
        *,
        direction: str,
    ) -> SimulatedTrade | None:
        if context.instrument.supports_options:
            return None
        for trade in context.recent_closed_trades:
            if trade.direction != direction or trade.status != "CLOSED":
                continue
            if (trade.booked_pnl or trade.pnl) <= 0:
                continue
            return trade
        return None

    def _classify_stock_dow_structure(self, session: list[Candle], atr: float) -> tuple[str, str]:
        if len(session) < 3:
            return "neutral", "insufficient"

        swing_highs, swing_lows = self._detect_session_swings(session, atr)
        recent_highs = [price for _, price in swing_highs[-3:]]
        recent_lows = [price for _, price in swing_lows[-3:]]

        bullish_hh_hl = (
            len(recent_highs) >= 2
            and len(recent_lows) >= 2
            and recent_highs[-1] > recent_highs[-2]
            and recent_lows[-1] > recent_lows[-2]
        )
        bearish_lh_ll = (
            len(recent_highs) >= 2
            and len(recent_lows) >= 2
            and recent_highs[-1] < recent_highs[-2]
            and recent_lows[-1] < recent_lows[-2]
        )
        if bullish_hh_hl:
            return "bullish", "higher-high-higher-low"
        if bearish_lh_ll:
            return "bearish", "lower-high-lower-low"

        recent = session[-5:] if len(session) >= 5 else session
        up_closes = sum(1 for candle in recent if candle.close > candle.open)
        down_closes = sum(1 for candle in recent if candle.close < candle.open)
        displacement = session[-1].close - session[0].open
        if up_closes >= max(2, len(recent) - 1) and displacement > atr * 0.6:
            return "bullish", "early-uptrend"
        if down_closes >= max(2, len(recent) - 1) and displacement < -atr * 0.6:
            return "bearish", "early-downtrend"
        return "mixed", "transition"

    def _stock_dow_trend_allows(self, observation: Observation, *, option_type: str, strict: bool = False) -> bool:
        if option_type == "CE":
            if observation.stock_dow_bias == "bearish":
                return False
            if strict and observation.stock_dow_bias != "bullish":
                return False
            return True
        if observation.stock_dow_bias == "bullish":
            return False
        if strict and observation.stock_dow_bias != "bearish":
            return False
        return True

    def _classify_stock_nifty_context(self, context: StrategyContext, atr: float) -> tuple[str, str]:
        if context.instrument.supports_options:
            return "neutral", "unavailable"
        companion_session = context.companion_session_candles
        companion_current = context.companion_current_candle
        if not companion_session or companion_current is None:
            return "neutral", "unavailable"

        companion_ranges = [max(candle.high - candle.low, 0.01) for candle in companion_session[-20:]] or [max(atr, 1.0)]
        companion_atr = median(companion_ranges)
        companion_vwap_denominator = sum(max(candle.volume, 1.0) for candle in companion_session)
        companion_vwap = (
            sum(candle.close * max(candle.volume, 1.0) for candle in companion_session) / companion_vwap_denominator
            if companion_vwap_denominator
            else companion_current.close
        )
        fifteen = self._aggregate_session_candles(companion_session, 15)
        thirty = self._aggregate_session_candles(companion_session, 30)
        bias_15 = self._classify_higher_timeframe_bias(fifteen, companion_atr)
        bias_30 = self._classify_higher_timeframe_bias(thirty, companion_atr)
        higher_bias = bias_30 if bias_30.endswith("trend") and bias_30 == bias_15 else bias_15 if bias_15 != "neutral" else bias_30

        current = companion_current
        previous = context.companion_previous_day
        first_five = companion_session[: min(5, len(companion_session))]
        first_fifteen = companion_session[: min(15, len(companion_session))]
        prior_hour = companion_session[-60:] if len(companion_session) > 60 else companion_session
        mapped_buy_liquidity, mapped_sell_liquidity = self.build_liquidity_maps(
            companion_session,
            context.companion_previous_day_candles,
            previous,
            current.close,
            companion_atr,
        )
        buy_sweeps = self.detect_sweeps(
            companion_session,
            side="buy",
            previous_day_level=previous.high,
            opening_level=max(candle.high for candle in first_five) if first_five else current.high,
            first_fifteen_level=max(candle.high for candle in first_fifteen) if first_fifteen else current.high,
            prior_hour_level=max(candle.high for candle in prior_hour) if prior_hour else current.high,
            session_reference=max(candle.high for candle in companion_session[:-1]) if len(companion_session) > 1 else current.high,
            atr=companion_atr,
            extra_levels=mapped_buy_liquidity,
            allowed_families=None,
        )
        sell_sweeps = self.detect_sweeps(
            companion_session,
            side="sell",
            previous_day_level=previous.low,
            opening_level=min(candle.low for candle in first_five) if first_five else current.low,
            first_fifteen_level=min(candle.low for candle in first_fifteen) if first_fifteen else current.low,
            prior_hour_level=min(candle.low for candle in prior_hour) if prior_hour else current.low,
            session_reference=min(candle.low for candle in companion_session[:-1]) if len(companion_session) > 1 else current.low,
            atr=companion_atr,
            extra_levels=mapped_sell_liquidity,
            allowed_families=None,
        )
        recent_sell_sweep = next(
            (
                event
                for event in sell_sweeps
                if event.primary and event.reclaim_index is not None and event.reclaim_index >= max(0, len(companion_session) - 3)
            ),
            None,
        )
        recent_buy_sweep = next(
            (
                event
                for event in buy_sweeps
                if event.primary and event.reclaim_index is not None and event.reclaim_index >= max(0, len(companion_session) - 3)
            ),
            None,
        )

        if (
            recent_sell_sweep is not None
            and current.close > recent_sell_sweep.defended_level
            and current.close > companion_vwap
            and current.close >= current.open
        ):
            return "bullish", "sl_hunt_bullish_reversal"
        if (
            recent_buy_sweep is not None
            and current.close < recent_buy_sweep.defended_level
            and current.close < companion_vwap
            and current.close <= current.open
        ):
            return "bearish", "sl_hunt_bearish_reversal"
        if higher_bias in {"bullish_trend", "bullish_reversal"} and current.close >= companion_vwap:
            return "bullish", higher_bias
        if higher_bias in {"bearish_trend", "bearish_reversal"} and current.close <= companion_vwap:
            return "bearish", higher_bias
        if abs(current.close - companion_vwap) <= max(companion_atr * 0.18, 4.0):
            return "neutral", "nifty_value_churn"
        return "neutral", higher_bias or "neutral"

    def _stock_nifty_alignment(self, observation: Observation, *, option_type: str) -> str:
        if observation.stock_nifty_bias == "neutral":
            return "neutral"
        if option_type == "CE":
            return "aligned" if observation.stock_nifty_bias == "bullish" else "opposed"
        return "aligned" if observation.stock_nifty_bias == "bearish" else "opposed"

    def _stock_retracement_reclaim_confirmed(
        self,
        context: StrategyContext,
        observation: Observation,
        event: SweepEvent,
        *,
        option_type: str,
        reclaim_candle: Candle,
        current: Candle,
    ) -> bool:
        if context.instrument.supports_options:
            return True
        if event.reclaim_index is None:
            return False
        session = context.session_candles
        if len(session) <= event.reclaim_index + 2:
            return False
        post_reclaim_window = session[event.reclaim_index + 1 : -1]
        if not post_reclaim_window:
            return False
        if option_type == "CE":
            opposite_seen = any(candle.close < candle.open for candle in post_reclaim_window)
            defended_retest = any(
                candle.low <= event.defended_level + observation.atr * 0.12 for candle in post_reclaim_window
            )
            reclaim_back = current.close > max(candle.high for candle in post_reclaim_window)
            return (
                opposite_seen
                and defended_retest
                and reclaim_back
                and current.close > event.defended_level
                and current.close >= reclaim_candle.close - observation.atr * 0.08
            )
        opposite_seen = any(candle.close > candle.open for candle in post_reclaim_window)
        defended_retest = any(
            candle.high >= event.defended_level - observation.atr * 0.12 for candle in post_reclaim_window
        )
        reclaim_back = current.close < min(candle.low for candle in post_reclaim_window)
        return (
            opposite_seen
            and defended_retest
            and reclaim_back
            and current.close < event.defended_level
            and current.close <= reclaim_candle.close + observation.atr * 0.08
        )

    def _stock_continuation_setup_names(self) -> set[str]:
        return {
            "stock_breakout_pullback_long",
            "stock_breakout_pullback_short",
            "stock_first_pullback_trend_long",
            "stock_first_pullback_trend_short",
            "stock_early_retracement_reclaim_long",
            "stock_early_retracement_reclaim_short",
        }

    def _stock_trap_setup_names(self) -> set[str]:
        return {
            "bullish_reclaim_watch",
            "bearish_rejection_watch",
            "bullish_pullback_continuation",
            "bearish_pullback_continuation",
            "previous_close_reclaim_long",
            "previous_close_rejection_short",
        }

    def _recent_same_direction_stock_trade(
        self,
        context: StrategyContext,
        *,
        option_type: str,
    ) -> SimulatedTrade | None:
        if context.instrument.supports_options:
            return None
        allowed_directions = {"LONG_STOCK", "LONG_CALL"} if option_type == "CE" else {"SHORT_STOCK", "LONG_PUT"}
        for trade in context.recent_closed_trades:
            if trade.direction in allowed_directions:
                return trade
        return None

    def _has_fresh_primary_stock_sweep_since(
        self,
        context: StrategyContext,
        observation: Observation,
        *,
        option_type: str,
        since_time,
    ) -> bool:
        sweeps = observation.sell_sweeps if option_type == "CE" else observation.buy_sweeps
        session = context.session_candles
        if since_time is None:
            return True
        for event in sweeps:
            if not event.primary or event.reclaim_index is None:
                continue
            if not 0 <= event.reclaim_index < len(session):
                continue
            if session[event.reclaim_index].timestamp > since_time:
                return True
        return False

    def _stock_midday_churn_active(self, context: StrategyContext, observation: Observation) -> bool:
        if context.instrument.supports_options:
            return False
        current_time = context.current_candle.timestamp.time()
        if current_time < dt_time(11, 45) or current_time > dt_time(13, 30):
            return False
        return (
            observation.participation_state in {"fair_value_churn", "post_trend_balance"}
            or observation.range_state in {"balanced", "compressing"}
            or observation.overlap_ratio >= 0.58
        )

    def _stock_flat_box(self, context: StrategyContext, observation: Observation) -> bool:
        if context.instrument.supports_options:
            return False
        recent = context.session_candles[-6:]
        if len(recent) < 4:
            return False
        span = max(candle.high for candle in recent) - min(candle.low for candle in recent)
        median_body = median(abs(candle.close - candle.open) for candle in recent)
        return (
            observation.range_state == "compressing"
            or observation.overlap_ratio >= 0.62
            or span <= observation.atr * 1.15
            or median_body <= observation.atr * 0.2
        )

    def _stock_continuation_breakout_override(
        self,
        context: StrategyContext,
        observation: Observation,
        *,
        option_type: str,
    ) -> bool:
        if context.instrument.supports_options:
            return False
        session = context.session_candles
        if len(session) < 6:
            return False
        current = context.current_candle
        prior = session[-6:-1]
        if len(prior) < 4:
            return False
        prior_high = max(candle.high for candle in prior)
        prior_low = min(candle.low for candle in prior)
        current_range = max(current.high - current.low, 0.01)
        prior_volumes = [max(candle.volume, 1.0) for candle in prior]
        volume_expansion = max(current.volume, 1.0) >= median(prior_volumes) * 1.25
        strong_body = self.candle_strength(current) >= 0.68 and current_range >= observation.atr * 0.72
        if option_type == "CE":
            return (
                observation.strong_intent
                and volume_expansion
                and strong_body
                and current.close >= prior_high
                and current.close > current.open
            )
        return (
            observation.strong_intent
            and volume_expansion
            and strong_body
            and current.close <= prior_low
            and current.close < current.open
        )

    def _demote_stock_candidate(
        self,
        candidate: SetupCandidate,
        *,
        cap_score: float,
        note: str,
        rule_ids: list[str],
    ) -> SetupCandidate:
        candidate.score = min(candidate.score, cap_score)
        candidate.ready_to_enter = False
        if note not in candidate.notes:
            candidate.notes.append(note)
        if note not in candidate.event.notes:
            candidate.event.notes.append(note)
        for rule_id in rule_ids:
            if rule_id not in candidate.rule_ids:
                candidate.rule_ids.append(rule_id)
        return candidate

    def _refine_stock_candidate(
        self,
        context: StrategyContext,
        observation: Observation,
        candidate: SetupCandidate,
    ) -> SetupCandidate:
        if context.instrument.supports_options:
            return candidate

        current = context.current_candle
        session = context.session_candles
        body_quality = self.candle_strength(current)
        recent_reclaim = candidate.event.reclaim_index is not None and candidate.event.reclaim_index >= max(0, len(session) - 3)
        same_candle_reclaim = candidate.event.reclaim_index is not None and candidate.event.reclaim_index == candidate.event.sweep_index
        major_level = self._is_obvious_stop_pool_label(candidate.event.level_label)
        midday_churn = self._stock_midday_churn_active(context, observation)
        flat_box = self._stock_flat_box(context, observation)
        breakout_override = self._stock_continuation_breakout_override(
            context,
            observation,
            option_type=candidate.option_type,
        )
        nifty_alignment = self._stock_nifty_alignment(observation, option_type=candidate.option_type)
        is_continuation = candidate.setup_type in self._stock_continuation_setup_names()
        is_trap_setup = candidate.setup_type in self._stock_trap_setup_names()

        recent_same_side_trade = self._recent_same_direction_stock_trade(context, option_type=candidate.option_type)
        if recent_same_side_trade is not None:
            anchor_time = (
                recent_same_side_trade.exit_time
                or recent_same_side_trade.last_partial_exit_time
                or recent_same_side_trade.entry_time
            )
            if not self._has_fresh_primary_stock_sweep_since(
                context,
                observation,
                option_type=candidate.option_type,
                since_time=anchor_time,
            ):
                self._demote_stock_candidate(
                    candidate,
                    cap_score=45.0,
                    note="A same-side stock trade already consumed this structure, so wait for a brand-new primary liquidity sweep before re-entering.",
                    rule_ids=["R113", "R114"],
                )

        if is_trap_setup:
            if not candidate.event.primary:
                self._demote_stock_candidate(
                    candidate,
                    cap_score=44.0,
                    note="Trap or reclaim entries now demand a primary liquidity sweep instead of recycling minor retests.",
                    rule_ids=["R115", "R116"],
                )
            if not recent_reclaim:
                self._demote_stock_candidate(
                    candidate,
                    cap_score=46.0,
                    note="The reclaim is already stale, so stock mode will not keep weak trap ideas armed for long.",
                    rule_ids=["R117"],
                )
            if body_quality < 0.52 and not candidate.ready_to_enter:
                self._demote_stock_candidate(
                    candidate,
                    cap_score=45.0,
                    note="The reclaim candle body is too weak for stock-mode execution, so the setup stays sidelined.",
                    rule_ids=["R118"],
                )
            if same_candle_reclaim and not major_level:
                self._demote_stock_candidate(
                    candidate,
                    cap_score=44.0,
                    note="A same-candle micro reclaim is ignored unless it comes from a major liquidity shelf.",
                    rule_ids=["R119"],
                )

        if is_continuation:
            risk = max(abs(current.close - candidate.invalidation_level), observation.atr * 0.65, 0.01)
            room = abs(candidate.target_spot_price - current.close)
            if room < risk * 1.35:
                self._demote_stock_candidate(
                    candidate,
                    cap_score=45.0,
                    note="Continuation room is too tight relative to the required invalidation risk.",
                    rule_ids=["R120"],
                )
            if observation.day_type not in {"gap-and-go", "trend-day", "trap-day"} and not observation.strong_intent:
                self._demote_stock_candidate(
                    candidate,
                    cap_score=46.0,
                    note="Continuation setups now demand stronger session context instead of firing inside average stock drift.",
                    rule_ids=["R121"],
                )
            if flat_box and not breakout_override:
                self._demote_stock_candidate(
                    candidate,
                    cap_score=45.0,
                    note="Continuation is blocked inside a flat overlapping box unless price breaks out with strong body and volume.",
                    rule_ids=["R122", "R123"],
                )
            if nifty_alignment == "opposed":
                self._demote_stock_candidate(
                    candidate,
                    cap_score=45.0,
                    note=f"Nifty direction is opposing this stock continuation ({observation.stock_nifty_state}), so continuation must stand aside instead of fighting the index.",
                    rule_ids=["R128", "R129"],
                )
            elif nifty_alignment == "aligned":
                candidate.score = min(candidate.score + 5.0, 100.0)
                candidate.notes.append(
                    f"Nifty direction is aligned ({observation.stock_nifty_state}), which supports this stock continuation setup."
                )
                candidate.event.notes.append(
                    f"Nifty direction is aligned ({observation.stock_nifty_state}), which supports this stock continuation setup."
                )
                for rule_id in ["R130"]:
                    if rule_id not in candidate.rule_ids:
                        candidate.rule_ids.append(rule_id)

        if midday_churn:
            if is_continuation and not breakout_override:
                self._demote_stock_candidate(
                    candidate,
                    cap_score=45.0,
                    note="Midday balanced churn now blocks stock continuation unless the breakout is unusually clean and forceful.",
                    rule_ids=["R124", "R125"],
                )
            elif is_trap_setup and not (
                candidate.event.primary
                and recent_reclaim
                and body_quality >= 0.58
                and (not same_candle_reclaim or major_level)
            ):
                self._demote_stock_candidate(
                    candidate,
                    cap_score=46.0,
                    note="Midday stock trading now waits for only the cleanest primary sweep-and-reclaim structures.",
                    rule_ids=["R126", "R127"],
                )

        if is_trap_setup and nifty_alignment == "opposed":
            if not (
                candidate.event.primary
                and recent_reclaim
                and body_quality >= 0.62
                and candidate.event.quality in {"tradable", "explosive"}
            ):
                self._demote_stock_candidate(
                    candidate,
                    cap_score=47.0,
                    note=f"Nifty is moving the other way ({observation.stock_nifty_state}), so opposite-direction stock traps now need exceptional reclaim quality.",
                    rule_ids=["R131", "R132"],
                )
        elif is_trap_setup and nifty_alignment == "aligned" and candidate.event.primary:
            candidate.score = min(candidate.score + 3.0, 100.0)
            candidate.notes.append(
                f"Nifty direction is aligned ({observation.stock_nifty_state}), which adds confidence to this stock trap."
            )
            candidate.event.notes.append(
                f"Nifty direction is aligned ({observation.stock_nifty_state}), which adds confidence to this stock trap."
            )
            for rule_id in ["R133"]:
                if rule_id not in candidate.rule_ids:
                    candidate.rule_ids.append(rule_id)

        return candidate

    def _pending_setup_max_bars_for_context(self, context: StrategyContext) -> int:
        if not context.instrument.supports_options:
            return 3
        return self.pending_setup_max_bars

    def _effective_entry_thresholds(
        self,
        context: StrategyContext,
        observation: Observation,
        best: SetupCandidate | None,
    ) -> tuple[float, float, bool]:
        enter_threshold, arm_threshold, allow_only_exceptional = self.entry_thresholds_for_timestamp(
            context.current_candle.timestamp
        )
        if (
            best is not None
            and not context.instrument.supports_options
            and best.setup_type in self._stock_continuation_setup_names()
        ):
            enter_threshold -= 6.0
            arm_threshold -= 4.0
            if observation.day_type in {"gap-and-go", "trend-day"}:
                enter_threshold -= 2.0
                arm_threshold -= 2.0
            if observation.strong_intent:
                enter_threshold -= 2.0
                arm_threshold -= 1.0
        if not context.instrument.supports_options:
            if context.portfolio_order_count_estimate >= 70:
                enter_threshold += 10.0
                arm_threshold += 8.0
            elif context.portfolio_order_count_estimate >= 45:
                enter_threshold += 5.0
                arm_threshold += 4.0
        return max(0.0, enter_threshold), max(0.0, arm_threshold), allow_only_exceptional

    def _candle_range(self, candle: Candle) -> float:
        return max(candle.high - candle.low, 0.01)

    def _lower_wick_ratio(self, candle: Candle) -> float:
        lower_body = min(candle.open, candle.close)
        return max(lower_body - candle.low, 0.0) / self._candle_range(candle)

    def _upper_wick_ratio(self, candle: Candle) -> float:
        upper_body = max(candle.open, candle.close)
        return max(candle.high - upper_body, 0.0) / self._candle_range(candle)

    def _stock_early_retracement_score(
        self,
        context: StrategyContext,
        observation: Observation,
        *,
        option_type: str,
        pullback_window: list[Candle],
        anchor_window: list[Candle],
        current: Candle,
    ) -> tuple[float, list[str], list[str], float, float] | None:
        if context.instrument.supports_options or observation.weak_intent:
            return None
        if not self._stock_dow_trend_allows(observation, option_type=option_type, strict=True):
            return None
        if option_type == "CE" and observation.stock_nifty_bias == "bearish":
            return None
        if option_type == "PE" and observation.stock_nifty_bias == "bullish":
            return None

        atr = max(observation.atr, 0.01)
        opening_price = context.session_candles[0].open
        current_strength = self.candle_strength(current)
        if current_strength < 0.1:
            return None

        pullback_high = max(candle.high for candle in pullback_window)
        pullback_low = min(candle.low for candle in pullback_window)
        pullback_range = max(pullback_high - pullback_low, 0.01)
        anchor_high = max(candle.high for candle in anchor_window)
        anchor_low = min(candle.low for candle in anchor_window)
        impulse_range = max(anchor_high - anchor_low, 0.01)
        directional_extension_r = (
            (current.close - opening_price) / atr
            if option_type == "CE"
            else (opening_price - current.close) / atr
        )
        value_extension_r = (
            (current.close - observation.vwap) / atr
            if option_type == "CE"
            else (observation.vwap - current.close) / atr
        )
        if directional_extension_r < 0.55:
            return None
        if directional_extension_r > 4.2 and value_extension_r > 2.2:
            return None
        if pullback_range > max(atr * 1.7, impulse_range * 0.68):
            return None

        pullback_ranges = [self._candle_range(candle) for candle in pullback_window]
        latest_pullback = pullback_window[-1]
        prior_pullback = pullback_window[:-1]
        score = 52.0
        notes: list[str] = []
        rule_ids = ["R107", "R108", "R109", "R134", "R135", "R136"]

        if option_type == "CE":
            bearish_candles = [candle for candle in pullback_window if candle.close < candle.open]
            if not bearish_candles:
                return None
            bearish_bodies = [abs(candle.close - candle.open) for candle in bearish_candles]
            if len(bearish_bodies) >= 2 and bearish_bodies[-1] <= bearish_bodies[0] * 0.95:
                score += 8
                notes.append("Bearish pullback bodies are shrinking, showing seller exhaustion.")
            elif len(bearish_bodies) == 1 and bearish_bodies[0] <= atr * 0.75:
                score += 4
                notes.append("Only one controlled bearish pullback body is present, so selling pressure is not expanding.")
            else:
                score -= 8
            lower_wick_count = sum(1 for candle in pullback_window if self._lower_wick_ratio(candle) >= 0.22)
            if lower_wick_count >= 2:
                score += 8
                notes.append("Lower wicks appeared during the pullback, showing demand defended the retracement low.")
            elif lower_wick_count >= 1:
                score += 4
            latest_low_holding = latest_pullback.low >= min(candle.low for candle in prior_pullback) - atr * 0.06
            latest_range_cooling = pullback_ranges[-1] <= max(pullback_ranges[:-1]) * 1.08
            if latest_low_holding and latest_range_cooling:
                score += 9
                notes.append("Price stopped making impulsive lower lows and bearish range cooled.")
            else:
                score -= 8
            structure_held = pullback_low >= anchor_low + impulse_range * 0.28 or pullback_low >= observation.vwap - atr * 0.25
            if not structure_held:
                return None
            score += 7
            notes.append("Pullback stayed structurally controlled instead of breaking the trend base.")
            reclaim_zone = max(latest_pullback.close, pullback_low + pullback_range * 0.28)
            reclaim_attempt = current.close >= reclaim_zone and current.close > current.open and current.close >= latest_pullback.close
            vwap_supportive = current.close >= observation.vwap or pullback_low >= observation.vwap - atr * 0.18
            if not reclaim_attempt or not vwap_supportive:
                return None
            score += 10
            notes.append("Close started recovering above the small reclaim zone before a full breakout candle.")
            if observation.stock_nifty_bias in {"bullish", "neutral"}:
                score += 4
                notes.append(f"Nifty is {observation.stock_nifty_state}, so it is not opposing the early long reclaim.")
            defended_level = pullback_low
            invalidation = round(pullback_low - atr * 0.18, 2)
        else:
            bullish_candles = [candle for candle in pullback_window if candle.close > candle.open]
            if not bullish_candles:
                return None
            bullish_bodies = [abs(candle.close - candle.open) for candle in bullish_candles]
            if len(bullish_bodies) >= 2 and bullish_bodies[-1] <= bullish_bodies[0] * 0.95:
                score += 8
                notes.append("Bullish bounce bodies are shrinking, showing buyer exhaustion.")
            elif len(bullish_bodies) == 1 and bullish_bodies[0] <= atr * 0.75:
                score += 4
                notes.append("Only one controlled bullish bounce body is present, so buying pressure is not expanding.")
            else:
                score -= 8
            upper_wick_count = sum(1 for candle in pullback_window if self._upper_wick_ratio(candle) >= 0.22)
            if upper_wick_count >= 2:
                score += 8
                notes.append("Upper wicks appeared during the bounce, showing supply defended the retracement high.")
            elif upper_wick_count >= 1:
                score += 4
            latest_high_holding = latest_pullback.high <= max(candle.high for candle in prior_pullback) + atr * 0.06
            latest_range_cooling = pullback_ranges[-1] <= max(pullback_ranges[:-1]) * 1.08
            if latest_high_holding and latest_range_cooling:
                score += 9
                notes.append("Price failed to make impulsive higher highs and bullish range cooled.")
            else:
                score -= 8
            structure_held = pullback_high <= anchor_high - impulse_range * 0.28 or pullback_high <= observation.vwap + atr * 0.25
            if not structure_held:
                return None
            score += 7
            notes.append("Bounce stayed structurally controlled instead of breaking the downtrend base.")
            reclaim_zone = min(latest_pullback.close, pullback_high - pullback_range * 0.28)
            reclaim_attempt = current.close <= reclaim_zone and current.close < current.open and current.close <= latest_pullback.close
            vwap_supportive = current.close <= observation.vwap or pullback_high <= observation.vwap + atr * 0.18
            if not reclaim_attempt or not vwap_supportive:
                return None
            score += 10
            notes.append("Close started rejecting below the small rejection zone before a full breakdown candle.")
            if observation.stock_nifty_bias in {"bearish", "neutral"}:
                score += 4
                notes.append(f"Nifty is {observation.stock_nifty_state}, so it is not opposing the early short rejection.")
            defended_level = pullback_high
            invalidation = round(pullback_high + atr * 0.18, 2)

        if observation.strong_intent:
            score += 6
            notes.append("Morning impulse is strong, so the engine can accept an earlier retracement reclaim.")
        if observation.day_type in {"gap-and-go", "trend-day"}:
            score += 5
            notes.append("Trend-day context supports faster pullback timing.")
        if observation.two_sided_participation:
            score += 3
        ledger_adjustment, ledger_note, ledger_rules = self._liquidity_ledger_score_adjustment(
            observation,
            option_type=option_type,
        )
        score += ledger_adjustment
        if ledger_note:
            notes.append(ledger_note)
            rule_ids.extend(ledger_rules)
        return max(0.0, min(score, 100.0)), notes, rule_ids, defended_level, invalidation

    def _build_stock_early_retracement_reclaim_candidate(
        self,
        context: StrategyContext,
        observation: Observation,
        *,
        option_type: str,
        recent_same_side_winner: SimulatedTrade | None,
    ) -> SetupCandidate | None:
        if context.instrument.supports_options:
            return None
        session = context.session_candles
        if len(session) < 5:
            return None
        current = context.current_candle
        scored_options: list[tuple[float, list[str], list[str], float, float, list[Candle], list[Candle]]] = []
        for pullback_size in (2, 3):
            if len(session) < pullback_size + 3:
                continue
            pullback_window = session[-(pullback_size + 1) : -1]
            anchor_window = session[: -(pullback_size + 1)]
            if len(pullback_window) < 2 or len(anchor_window) < 2:
                continue
            scored = self._stock_early_retracement_score(
                context,
                observation,
                option_type=option_type,
                pullback_window=pullback_window,
                anchor_window=anchor_window,
                current=current,
            )
            if scored is not None:
                score, notes, rule_ids, defended_level, invalidation = scored
                if pullback_size == 2:
                    score += 5
                    notes.append("Two-candle pullback is enough here, so stock mode does not wait for a third confirmation candle.")
                    rule_ids.append("R139")
                scored_options.append((score, notes, rule_ids, defended_level, invalidation, pullback_window, anchor_window))
        if not scored_options:
            return None
        score, notes, rule_ids, defended_level, invalidation, pullback_window, _anchor_window = max(
            scored_options,
            key=lambda item: item[0],
        )
        if recent_same_side_winner is not None:
            score += 4
            notes.append("Recent same-side winner confirms this stock is respecting early trend-pullback entries.")
            rule_ids.append("R110")

        risk = max(abs(current.close - invalidation), observation.atr * 0.7)
        if option_type == "CE":
            target_spot = self.next_upside_target(context, current.close, risk)
            room = target_spot - current.close
            if room < risk * 1.45:
                return None
            first_target = current.close + risk
            setup_type = "stock_early_retracement_reclaim_long"
            direction = "LONG_CALL"
            side = "sell"
            level_label = f"Early Retracement Demand {defended_level:.2f}"
            sweep_price = min(candle.low for candle in pullback_window)
            trigger_basis = "early_close_above"
        else:
            target_spot = self.next_downside_target(context, current.close, risk)
            room = current.close - target_spot
            if room < risk * 1.45:
                return None
            first_target = current.close - risk
            setup_type = "stock_early_retracement_reclaim_short"
            direction = "LONG_PUT"
            side = "buy"
            level_label = f"Early Retracement Supply {defended_level:.2f}"
            sweep_price = max(candle.high for candle in pullback_window)
            trigger_basis = "early_close_below"

        score += 6
        notes.append("Early retracement reclaim mode enters full quantity now, before waiting for expansion confirmation.")
        notes.append("Turnover filter remains the separate hard liquidity gate before execution.")
        rule_ids.extend(["R137", "R138"])
        event = SweepEvent(
            side=side,
            level_label=level_label,
            level_price=round(defended_level, 2),
            sweep_index=max(len(session) - len(pullback_window) - 1, 0),
            reclaim_index=len(session) - 1,
            trigger_index=len(session) - 1,
            sweep_price=round(sweep_price, 2),
            defended_level=round(defended_level, 2),
            trigger_price=round(current.close, 2),
            invalidation_level=round(invalidation, 2),
            primary=True,
            quality="tradable",
            notes=list(notes),
        )
        return SetupCandidate(
            setup_type=setup_type,
            direction=direction,
            option_type=option_type,
            trigger_basis=trigger_basis,
            trigger_price=round(current.close, 2),
            invalidation_level=round(invalidation, 2),
            defended_level=round(defended_level, 2),
            target_spot_price=round(target_spot, 2),
            first_target_price=round(first_target, 2),
            score=max(0.0, min(score, 100.0)),
            ready_to_enter=True,
            notes=notes,
            rule_ids=list(dict.fromkeys(rule_ids)),
            event=event,
        )

    def _build_stock_first_pullback_candidate(
        self,
        context: StrategyContext,
        observation: Observation,
        *,
        option_type: str,
        recent_same_side_winner: SimulatedTrade | None,
    ) -> SetupCandidate | None:
        if context.instrument.supports_options:
            return None
        session = context.session_candles
        if len(session) < 6:
            return None
        current = context.current_candle
        current_strength = self.candle_strength(current)
        if current_strength < 0.45:
            return None

        pullback_window = session[-4:-1]
        anchor_window = session[:-4]
        if len(pullback_window) < 3 or len(anchor_window) < 2:
            return None

        pullback_up_closes = sum(1 for candle in pullback_window if candle.close > candle.open)
        pullback_down_closes = sum(1 for candle in pullback_window if candle.close < candle.open)
        pullback_high = max(candle.high for candle in pullback_window)
        pullback_low = min(candle.low for candle in pullback_window)
        pullback_close_floor = min(candle.close for candle in pullback_window)
        pullback_close_ceiling = max(candle.close for candle in pullback_window)
        pullback_range = max(pullback_high - pullback_low, 0.01)
        opening_price = session[0].open
        directional_extension_r = (
            (current.close - opening_price) / max(observation.atr, 0.01)
            if option_type == "CE"
            else (opening_price - current.close) / max(observation.atr, 0.01)
        )
        opening_phase = observation.session_phase in {"opening-map", "primary-trap-window"}
        minimum_trend_extension_r = 0.35

        if option_type == "CE":
            if not self._stock_dow_trend_allows(observation, option_type=option_type, strict=True):
                return None
            if directional_extension_r < minimum_trend_extension_r:
                return None
            if opening_phase and observation.opening_confirmation == "gap-down-confirmed":
                return None
            if (
                opening_phase
                and observation.previous_day_bias.startswith("bearish")
                and current.close < observation.opening_range_high
                and observation.crowding_bias != "short-comfort"
            ):
                return None
            if observation.day_type not in {"gap-and-go", "trend-day", "trap-day"} and directional_extension_r < 2.2:
                return None
            if current.close <= current.open or current.close <= observation.vwap:
                return None
            if pullback_down_closes < 1:
                return None
            if current.close < pullback_close_ceiling - observation.atr * 0.08:
                return None
            if pullback_range > max(observation.atr * 1.45, abs(opening_price - current.close) * 0.55):
                return None
            defended_level = pullback_low
            trigger_price = max(candle.high for candle in pullback_window)
            if current.close < pullback_close_ceiling - observation.atr * 0.12:
                return None
            invalidation = round(pullback_low - observation.atr * 0.18, 2)
            risk = max(current.close - invalidation, observation.atr * 0.7)
            target_spot = self.next_upside_target(context, current.close, risk)
            score = 66.0
            notes = [
                "Strong stock uptrend printed a shallow first pullback and resumed without needing a full trap reversal.",
                "Trend-following stock mode accepts the first defended retracement when momentum and structure stay aligned.",
            ]
            setup_type = "stock_first_pullback_trend_long"
            direction = "LONG_CALL"
            level_label = f"First Pullback Demand {defended_level:.2f}"
            sweep_price = round(pullback_low, 2)
        else:
            if not self._stock_dow_trend_allows(observation, option_type=option_type, strict=True):
                return None
            if directional_extension_r < minimum_trend_extension_r:
                return None
            if opening_phase and observation.opening_confirmation in {"gap-up-confirmed", "gap-up-trap-risk"}:
                return None
            if (
                opening_phase
                and observation.previous_day_bias.startswith("bullish")
                and current.close > observation.opening_range_low
                and observation.crowding_bias != "long-comfort"
            ):
                return None
            if observation.day_type not in {"gap-and-go", "trend-day", "trap-day"} and directional_extension_r < 2.2:
                return None
            if current.close >= current.open or current.close >= observation.vwap:
                return None
            if pullback_up_closes < 1:
                return None
            if current.close > pullback_close_floor + observation.atr * 0.12:
                return None
            if pullback_range > max(observation.atr * 1.45, abs(opening_price - current.close) * 0.55):
                return None
            defended_level = pullback_high
            trigger_price = min(candle.low for candle in pullback_window)
            invalidation = round(pullback_high + observation.atr * 0.18, 2)
            risk = max(invalidation - current.close, observation.atr * 0.7)
            target_spot = self.next_downside_target(context, current.close, risk)
            score = 66.0
            notes = [
                "Strong stock downtrend printed a shallow first pullback and resumed without needing a full trap rejection.",
                "Trend-following stock mode accepts the first defended retracement when momentum and structure stay aligned.",
            ]
            setup_type = "stock_first_pullback_trend_short"
            direction = "LONG_PUT"
            level_label = f"First Pullback Supply {defended_level:.2f}"
            sweep_price = round(pullback_high, 2)

        rule_ids = ["R107", "R108", "R109", "R111", "R112"]
        if recent_same_side_winner is not None:
            score += 6
            notes.append("Recent same-side winner confirms this stock is still respecting trend continuation entries.")
            rule_ids.append("R110")
        if observation.strong_intent:
            score += 8
            notes.append("Directional intent remains strong, so the first pullback continuation deserves more trust.")
        if observation.day_type == "gap-and-go":
            score += 6
            notes.append("Gap-and-go day type supports earlier continuation participation in stock mode.")
        if observation.value_state == "discount" and option_type == "PE":
            score += 5
            notes.append("Discount pricing is acceptable here because stock mode prioritizes trend continuation over mean-reversion on strong losers.")
        if observation.value_state == "inflated" and option_type == "CE":
            score += 5
            notes.append("Inflated pricing is acceptable here because stock mode prioritizes trend continuation over fade logic on strong gainers.")
        if observation.two_sided_participation:
            score += 3
        ledger_adjustment, ledger_note, ledger_rules = self._liquidity_ledger_score_adjustment(
            observation,
            option_type=option_type,
        )
        score += ledger_adjustment
        if ledger_note:
            notes.append(ledger_note)
            rule_ids.extend(ledger_rules)
        room = abs(target_spot - current.close)
        if room >= risk * 1.45:
            score += 6
            notes.append("There is still enough room left for the trend leg to continue cleanly.")
        else:
            score -= 8
            notes.append("The move is already mature, so the first-pullback continuation needs tighter expectations.")

        event = SweepEvent(
            side="sell" if option_type == "CE" else "buy",
            level_label=level_label,
            level_price=round(defended_level, 2),
            sweep_index=max(len(session) - 4, 0),
            reclaim_index=len(session) - 1,
            trigger_index=len(session) - 1,
            sweep_price=sweep_price,
            defended_level=round(defended_level, 2),
            trigger_price=round(current.close, 2),
            invalidation_level=invalidation,
            primary=True,
            quality="tradable",
            notes=list(notes),
        )
        return SetupCandidate(
            setup_type=setup_type,
            direction=direction,
            option_type=option_type,
            trigger_basis="close_above" if option_type == "CE" else "close_below",
            trigger_price=round(current.close, 2),
            invalidation_level=invalidation,
            defended_level=round(defended_level, 2),
            target_spot_price=round(target_spot, 2),
            first_target_price=round(current.close + risk, 2) if option_type == "CE" else round(current.close - risk, 2),
            score=max(0.0, min(score, 100.0)),
            ready_to_enter=True,
            notes=notes,
            rule_ids=rule_ids,
            event=event,
        )

    def build_stock_continuation_candidates(self, context: StrategyContext, observation: Observation) -> list[SetupCandidate]:
        if context.instrument.supports_options:
            return []
        session = context.session_candles
        if len(session) < 6:
            return []
        current = context.current_candle
        recent = session[-4:]
        anchor_window = session[-8:-3]
        if not anchor_window:
            return []
        current_strength = self.candle_strength(current)
        recent_pullback_low = min(candle.low for candle in recent)
        recent_pullback_high = max(candle.high for candle in recent)
        breakout_shelf_high = max(candle.high for candle in anchor_window)
        breakout_shelf_low = min(candle.low for candle in anchor_window)
        bullish_candidate: SetupCandidate | None = None
        bearish_candidate: SetupCandidate | None = None

        recent_bullish_winner = self._recent_profitable_stock_trade(context, direction="LONG_STOCK")
        recent_bearish_winner = self._recent_profitable_stock_trade(context, direction="SHORT_STOCK")

        strong_bullish_trend = (
            observation.day_type in {"gap-and-go", "trend-day"}
            or (current.close - session[0].open) > observation.atr * 2.0
        )
        bullish_pullback_holding = (
            self._stock_dow_trend_allows(observation, option_type="CE", strict=True)
            and len(session) >= 8
            and strong_bullish_trend
            and recent_pullback_low >= breakout_shelf_high - observation.atr * 0.4
            and current.close >= breakout_shelf_high
            and current.close > current.open
            and current_strength >= 0.42
            and (
                observation.value_state != "inflated"
                or (observation.day_type in {"gap-and-go", "trend-day"} and observation.strong_intent)
            )
        )
        if bullish_pullback_holding:
            score = 62.0
            notes = [
                "Breakout pullback held above the recent breakout shelf without needing a brand-new full sweep.",
                "Stock mode allows same-trend continuation entries in strong gainers once the first defended pullback is accepted.",
            ]
            rule_ids = ["R107", "R108", "R109"]
            if recent_bullish_winner is not None:
                score += 8
                notes.append("A recent profitable long was stopped or closed, so same-trend re-entry is allowed on the first clean pullback.")
                rule_ids.append("R110")
            if observation.strong_intent:
                score += 6
                notes.append("Strong directional intent makes this continuation setup more trustworthy in stock mode.")
            if observation.two_sided_participation:
                score += 3
            if observation.stop_availability == "partially-cleared":
                score += 3
            if observation.value_state == "inflated":
                score += 4
                notes.append("Inflated pricing is acceptable because the trend is still acting cleanly and stock mode allows momentum continuation.")
            ledger_adjustment, ledger_note, ledger_rules = self._liquidity_ledger_score_adjustment(
                observation,
                option_type="CE",
            )
            score += ledger_adjustment
            if ledger_note:
                notes.append(ledger_note)
                rule_ids.extend(ledger_rules)
            risk = max(current.close - recent_pullback_low, observation.atr * 0.75)
            target_spot = self.next_upside_target(context, current.close, risk)
            room = target_spot - current.close
            if room >= risk * 1.5:
                score += 8
                notes.append("There is still enough room for continuation toward the next liquidity shelf.")
            else:
                score -= 10
                notes.append("Continuation room is too tight versus the pullback risk.")
            event = SweepEvent(
                side="sell",
                level_label=f"Breakout Pullback Shelf {breakout_shelf_high:.2f}",
                level_price=round(breakout_shelf_high, 2),
                sweep_index=max(len(session) - 4, 0),
                reclaim_index=len(session) - 1,
                trigger_index=len(session) - 1,
                sweep_price=round(recent_pullback_low, 2),
                defended_level=round(breakout_shelf_high, 2),
                trigger_price=round(current.close, 2),
                invalidation_level=round(recent_pullback_low - observation.atr * 0.18, 2),
                primary=True,
                quality="tradable",
                notes=list(notes),
            )
            bullish_candidate = SetupCandidate(
                setup_type="stock_breakout_pullback_long",
                direction="LONG_CALL",
                option_type="CE",
                trigger_basis="close_above",
                trigger_price=round(max(breakout_shelf_high, current.low), 2),
                invalidation_level=round(recent_pullback_low - observation.atr * 0.18, 2),
                defended_level=round(breakout_shelf_high, 2),
                target_spot_price=round(target_spot, 2),
                first_target_price=round(current.close + risk, 2),
                score=max(0.0, min(score, 100.0)),
                ready_to_enter=True,
                notes=notes,
                rule_ids=rule_ids,
                event=event,
            )

        strong_bearish_trend = (
            observation.day_type in {"gap-and-go", "trend-day"}
            or (session[0].open - current.close) > observation.atr * 2.0
        )
        bearish_pullback_holding = (
            self._stock_dow_trend_allows(observation, option_type="PE", strict=True)
            and len(session) >= 8
            and strong_bearish_trend
            and recent_pullback_high <= breakout_shelf_low + observation.atr * 0.4
            and current.close <= breakout_shelf_low
            and current.close < current.open
            and current_strength >= 0.42
            and (
                observation.value_state != "discount"
                or (observation.day_type in {"gap-and-go", "trend-day"} and observation.strong_intent)
            )
        )
        if bearish_pullback_holding:
            score = 62.0
            notes = [
                "Breakdown pullback held below the recent breakdown shelf without needing a brand-new full sweep.",
                "Stock mode allows same-trend continuation entries in strong losers once the first defended pullback is accepted.",
            ]
            rule_ids = ["R107", "R108", "R109"]
            if recent_bearish_winner is not None:
                score += 8
                notes.append("A recent profitable short was stopped or closed, so same-trend re-entry is allowed on the first clean pullback.")
                rule_ids.append("R110")
            if observation.strong_intent:
                score += 6
                notes.append("Strong directional intent makes this continuation setup more trustworthy in stock mode.")
            if observation.two_sided_participation:
                score += 3
            if observation.stop_availability == "partially-cleared":
                score += 3
            if observation.value_state == "discount":
                score += 4
                notes.append("Discount pricing is acceptable because the trend is still acting cleanly and stock mode allows momentum continuation.")
            ledger_adjustment, ledger_note, ledger_rules = self._liquidity_ledger_score_adjustment(
                observation,
                option_type="PE",
            )
            score += ledger_adjustment
            if ledger_note:
                notes.append(ledger_note)
                rule_ids.extend(ledger_rules)
            risk = max(recent_pullback_high - current.close, observation.atr * 0.75)
            target_spot = self.next_downside_target(context, current.close, risk)
            room = current.close - target_spot
            if room >= risk * 1.5:
                score += 8
                notes.append("There is still enough room for continuation toward the next downside liquidity shelf.")
            else:
                score -= 10
                notes.append("Continuation room is too tight versus the pullback risk.")
            event = SweepEvent(
                side="buy",
                level_label=f"Breakdown Pullback Shelf {breakout_shelf_low:.2f}",
                level_price=round(breakout_shelf_low, 2),
                sweep_index=max(len(session) - 4, 0),
                reclaim_index=len(session) - 1,
                trigger_index=len(session) - 1,
                sweep_price=round(recent_pullback_high, 2),
                defended_level=round(breakout_shelf_low, 2),
                trigger_price=round(current.close, 2),
                invalidation_level=round(recent_pullback_high + observation.atr * 0.18, 2),
                primary=True,
                quality="tradable",
                notes=list(notes),
            )
            bearish_candidate = SetupCandidate(
                setup_type="stock_breakout_pullback_short",
                direction="LONG_PUT",
                option_type="PE",
                trigger_basis="close_below",
                trigger_price=round(min(breakout_shelf_low, current.high), 2),
                invalidation_level=round(recent_pullback_high + observation.atr * 0.18, 2),
                defended_level=round(breakout_shelf_low, 2),
                target_spot_price=round(target_spot, 2),
                first_target_price=round(current.close - risk, 2),
                score=max(0.0, min(score, 100.0)),
                ready_to_enter=True,
                notes=notes,
                rule_ids=rule_ids,
                event=event,
            )

        first_pullback_long = self._build_stock_first_pullback_candidate(
            context,
            observation,
            option_type="CE",
            recent_same_side_winner=recent_bullish_winner,
        )
        first_pullback_short = self._build_stock_first_pullback_candidate(
            context,
            observation,
            option_type="PE",
            recent_same_side_winner=recent_bearish_winner,
        )
        early_reclaim_long = self._build_stock_early_retracement_reclaim_candidate(
            context,
            observation,
            option_type="CE",
            recent_same_side_winner=recent_bullish_winner,
        )
        early_reclaim_short = self._build_stock_early_retracement_reclaim_candidate(
            context,
            observation,
            option_type="PE",
            recent_same_side_winner=recent_bearish_winner,
        )

        return [
            candidate
            for candidate in (
                bullish_candidate,
                bearish_candidate,
                first_pullback_long,
                first_pullback_short,
                early_reclaim_long,
                early_reclaim_short,
            )
            if candidate is not None
        ]

    def _nifty_round_distance_adjustment(self, observation: Observation, level_label: str, sweep_price: float, level_price: float) -> tuple[float, str | None]:
        if "round number" not in level_label.lower():
            return 0.0, None
        distance = abs(sweep_price - level_price)
        if distance <= 6:
            return 7.0, "Sweep tagged the round number very tightly, which strengthens the Nifty reversal read."
        if distance <= 10:
            return 5.0, "Sweep stayed very close to the round number, which still gives strong liquidity relevance."
        if distance <= 15:
            return 2.0, "Sweep was near the round number, but not perfectly tight."
        if distance <= 20:
            return -2.0, "Sweep front-ran the round number a bit, so conviction is slightly reduced."
        return -6.0, "Sweep missed the round number by too much, so the liquidity read is less precise."

    def _nifty_retest_quality_adjustment(
        self,
        context: StrategyContext,
        observation: Observation,
        event: SweepEvent,
        option_type: str,
        reclaim_candle: Candle,
    ) -> tuple[float, str | None]:
        if not self._is_nifty_mode(context) or event.reclaim_index is None:
            return 0.0, None
        retest_slice = context.session_candles[event.reclaim_index + 1 : min(len(context.session_candles), event.reclaim_index + 3)]
        if not retest_slice:
            return 0.0, None
        reclaimed_range = max(abs(reclaim_candle.close - event.defended_level), observation.atr * 0.15)
        if option_type == "CE":
            deepest_retest = min(candle.low for candle in retest_slice)
            retest_depth = max(0.0, event.defended_level - deepest_retest)
            held = all(candle.close >= event.defended_level - observation.atr * 0.05 for candle in retest_slice)
        else:
            highest_retest = max(candle.high for candle in retest_slice)
            retest_depth = max(0.0, highest_retest - event.defended_level)
            held = all(candle.close <= event.defended_level + observation.atr * 0.05 for candle in retest_slice)
        if held and retest_depth <= max(observation.atr * 0.18, reclaimed_range * 0.45):
            return 7.0, "Retest stayed shallow and held, which improves the Nifty trap quality."
        if retest_depth >= max(observation.atr * 0.45, reclaimed_range * 0.95):
            return -8.0, "Retest dug too deeply back into the level, so the Nifty reversal is less clean."
        return 0.0, None

    def _nifty_htf_score_adjustment(self, context: StrategyContext, observation: Observation, option_type: str) -> tuple[float, str | None]:
        if not self._is_nifty_mode(context):
            return 0.0, None
        htf = observation.higher_timeframe_context
        if option_type == "CE":
            if htf == "bullish_trend":
                return 6.0, "Higher timeframe bias is bullish, so the Nifty long setup is aligned."
            if htf == "bullish_reversal":
                return 4.0, "Higher timeframe has already shifted into bullish reversal context."
            if htf == "range":
                return -2.0, "Higher timeframe is still range-bound, so this Nifty long needs extra care."
        else:
            if htf == "bearish_trend":
                return 6.0, "Higher timeframe bias is bearish, so the Nifty short setup is aligned."
            if htf == "bearish_reversal":
                return 4.0, "Higher timeframe has already shifted into bearish reversal context."
            if htf == "range":
                return -2.0, "Higher timeframe is still range-bound, so this Nifty short needs extra care."
        return 0.0, None

    def _companion_bank_confirmation(
        self,
        bank_session: list[Candle],
        bank_round: float,
        option_type: str,
        bank_atr: float,
    ) -> tuple[bool, list[str]]:
        if len(bank_session) < 4:
            return False, []
        notes: list[str] = []
        if option_type == "CE":
            probe_index = min(range(len(bank_session)), key=lambda index: bank_session[index].low)
            reclaim_index = next(
                (
                    index
                    for index in range(probe_index, len(bank_session))
                    if bank_session[index].close > bank_round and bank_session[index].close > bank_session[index].open
                ),
                None,
            )
            if reclaim_index is None:
                return False, notes
            reclaim_candle = bank_session[reclaim_index]
            if self.candle_strength(reclaim_candle) < 0.5:
                return False, notes
            follow_through = any(
                candle.close >= max(bank_round, reclaim_candle.close - bank_atr * 0.08)
                and candle.high >= reclaim_candle.high - bank_atr * 0.1
                and self.candle_strength(candle) >= 0.42
                for candle in bank_session[reclaim_index + 1 : reclaim_index + 3]
            )
            if not follow_through:
                return False, notes
            notes.extend(
                [
                    "Bank Nifty reclaimed the round number with a strong body.",
                    "Bank Nifty follow-through held after the reclaim, so the confirmation is not just a touch-and-bounce.",
                ]
            )
            return True, notes
        probe_index = max(range(len(bank_session)), key=lambda index: bank_session[index].high)
        reclaim_index = next(
            (
                index
                for index in range(probe_index, len(bank_session))
                if bank_session[index].close < bank_round and bank_session[index].close < bank_session[index].open
            ),
            None,
        )
        if reclaim_index is None:
            return False, notes
        reclaim_candle = bank_session[reclaim_index]
        if self.candle_strength(reclaim_candle) < 0.5:
            return False, notes
        follow_through = any(
            candle.close <= min(bank_round, reclaim_candle.close + bank_atr * 0.08)
            and candle.low <= reclaim_candle.low + bank_atr * 0.1
            and self.candle_strength(candle) >= 0.42
            for candle in bank_session[reclaim_index + 1 : reclaim_index + 3]
        )
        if not follow_through:
            return False, notes
        notes.extend(
            [
                "Bank Nifty rejected the round number with a strong body.",
                "Bank Nifty follow-through held after the rejection, so the confirmation is not just a touch-and-bounce.",
            ]
        )
        return True, notes

    def _allowed_liquidity_families_for_context(self, context: StrategyContext) -> tuple[str, ...] | None:
        if context.instrument.symbol == "NIFTY" and context.instrument.supports_options:
            return ("previous day high", "previous day low", "round number", "session extreme")
        return None

    def _filter_liquidity_levels(
        self,
        levels: list[tuple[str, float, bool]],
        allowed_families: tuple[str, ...] | None,
    ) -> list[tuple[str, float, bool]]:
        if allowed_families is None:
            return levels
        return [level for level in levels if self._label_family(level[0]) in allowed_families]

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
        allowed_families: tuple[str, ...] | None = None,
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
            if allowed_families is not None and self._label_family(level_label) not in allowed_families:
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

    def entry_thresholds_for_timestamp(self, timestamp) -> tuple[float, float, bool]:
        current_time = timestamp.time()
        if current_time >= dt_time(15, 0):
            return 999.0, 999.0, True
        return self.enter_threshold, self.arm_threshold, False

    def _is_obvious_stop_pool_label(self, label: str) -> bool:
        lowered = label.lower()
        obvious_families = (
            "previous day high",
            "previous day low",
            "opening range high",
            "opening range low",
            "first 15m high",
            "first 15m low",
            "equal high cluster",
            "equal low cluster",
            "round number",
            "pivot point",
            "pivot r1",
            "pivot r2",
            "pivot s1",
            "pivot s2",
            "previous-day swing high",
            "previous-day swing low",
            "previous-day resistance shelf",
            "previous-day support shelf",
        )
        return lowered.startswith(obvious_families)

    def _opening_shock_metrics(self, session: list[Candle], observation: Observation) -> tuple[float, float, bool]:
        if not session:
            return 0.0, 0.0, False
        first_candle = session[0]
        atr = max(observation.atr, 0.01)
        first_candle_range_r = (first_candle.high - first_candle.low) / atr
        gap_r = abs(observation.gap) / atr
        opening_shock = first_candle_range_r >= 4.5 or gap_r >= 3.5
        return first_candle_range_r, gap_r, opening_shock

    def _is_gap_reset_trap_direction(self, observation: Observation, option_type: str) -> bool:
        return (
            option_type == "CE" and observation.opening_confirmation == "gap-up-trap-risk"
        ) or (
            option_type == "PE" and observation.opening_confirmation == "gap-down-trap-risk"
        )

    def _is_major_upper_reference_label(self, label: str) -> bool:
        lowered = label.lower()
        upper_reference_families = (
            "previous day high",
            "opening range high",
            "first 15m high",
            "prior hour high",
            "equal high cluster",
            "round number",
            "pivot point",
            "pivot r1",
            "pivot r2",
            "previous-day swing high",
            "previous-day resistance shelf",
        )
        return lowered.startswith(upper_reference_families)

    def _classify_reclaim_setup_type(
        self,
        *,
        option_type: str,
        event: SweepEvent,
        confluence_labels: list[str],
        session_length: int,
    ) -> str:
        meaningful_sweep = event.primary or self._is_obvious_stop_pool_label(event.level_label) or any(
            self._is_obvious_stop_pool_label(label) for label in confluence_labels
        )
        fresh_primary_sweep = event.primary and event.reclaim_index is not None and event.reclaim_index >= max(0, session_length - 3)
        if option_type == "CE":
            return "bullish_reclaim_watch" if fresh_primary_sweep else "bullish_pullback_continuation"
        if fresh_primary_sweep:
            return "bearish_rejection_watch"
        return "bearish_rejection_watch" if meaningful_sweep and event.primary else "bearish_pullback_continuation"

    def decide_entry(self, context: StrategyContext, observation: Observation, candidates: list[SetupCandidate] | None = None) -> TradeDecision:
        candidates = candidates if candidates is not None else self.build_candidates(context, observation)
        if context.pending_setup is not None:
            pending_decision = self.evaluate_pending_setup(context, observation, candidates)
            if pending_decision is not None:
                return pending_decision
        if self._is_nifty_mode(context) and observation.nifty_mid_noise:
            return TradeDecision(
                action=TradeAction.no_trade,
                confidence=0.44,
                reason=(
                    "Nifty is stuck in low-ATR overlapping noise between nearby liquidity, "
                    "so heuristic mode skips fresh entries until a cleaner displacement appears."
                ),
                decision_source="heuristic",
                market_state=observation.day_type,
                rule_ids_used=["R21", "R29", "R50", "R55", "R56", "R95"],
            )

        best = self.select_best_candidate(candidates)
        enter_threshold, arm_threshold, allow_only_exceptional = self._effective_entry_thresholds(
            context,
            observation,
            best,
        )
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

        if allow_only_exceptional and not (
            best.ready_to_enter and best.score >= enter_threshold and best.event.primary and best.event.quality in {"tradable", "explosive"}
        ):
            return TradeDecision(
                action=TradeAction.no_trade,
                confidence=min(0.8, best.score / 100),
                reason="After 15:00 heuristic mode does not open fresh trades, so this setup is skipped.",
                decision_source="heuristic",
                market_state=observation.day_type,
                setup_score=round(best.score, 2),
                setup_type=best.setup_type,
                rule_ids_used=list(dict.fromkeys(best.rule_ids + ["R60", "R74", "R91", "R99"])),
            )

        if best.ready_to_enter and best.score >= enter_threshold:
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

        if best.score >= arm_threshold and not allow_only_exceptional:
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
        for event in observation.sell_sweeps[:3]:
            candidate = self.build_candidate_from_event(context, observation, event, option_type="CE", direction="LONG_CALL")
            if candidate is not None:
                candidates.append(candidate)
        for event in observation.buy_sweeps[:3]:
            candidate = self.build_candidate_from_event(context, observation, event, option_type="PE", direction="LONG_PUT")
            if candidate is not None:
                candidates.append(candidate)
        previous_close_candidates = (
            []
            if self._allowed_liquidity_families_for_context(context) is not None
            else self.build_previous_close_candidates(context, observation)
        )
        candidates.extend(previous_close_candidates)
        candidates.extend(self.build_stock_continuation_candidates(context, observation))
        companion_candidates = self.build_companion_index_candidates(context, observation)
        candidates.extend(companion_candidates)
        if not context.instrument.supports_options:
            stock_bias = (context.stock_trade_bias or "both").strip().lower()
            if stock_bias == "long":
                candidates = [candidate for candidate in candidates if candidate.option_type == "CE"]
            elif stock_bias == "short":
                candidates = [candidate for candidate in candidates if candidate.option_type == "PE"]
            candidates = [self._refine_stock_candidate(context, observation, candidate) for candidate in candidates]
        return candidates

    def build_companion_index_candidates(self, context: StrategyContext, observation: Observation) -> list[SetupCandidate]:
        if context.instrument.symbol != "NIFTY" or context.companion_symbol != "BANKNIFTY":
            return []
        if not context.companion_session_candles or context.companion_current_candle is None:
            return []
        bullish = self._build_companion_round_candidate(context, observation, option_type="CE", direction="LONG_CALL")
        bearish = self._build_companion_round_candidate(context, observation, option_type="PE", direction="LONG_PUT")
        return [candidate for candidate in (bullish, bearish) if candidate is not None]

    def _build_companion_round_candidate(
        self,
        context: StrategyContext,
        observation: Observation,
        *,
        option_type: str,
        direction: str,
    ) -> SetupCandidate | None:
        nifty_session = context.session_candles
        bank_session = context.companion_session_candles
        current = context.current_candle
        bank_current = context.companion_current_candle
        if len(nifty_session) < 3 or len(bank_session) < 3 or bank_current is None:
            return None

        bank_recent = bank_session[-5:]
        nifty_recent = nifty_session[-5:]
        nifty_step = self._round_number_step(current.close)
        bank_step = self._round_number_step(bank_current.close)
        bank_ranges = [max(candle.high - candle.low, 0.01) for candle in bank_session[-20:]] or [1.0]
        bank_atr = median(bank_ranges)
        nifty_near_tolerance = max(nifty_step * 0.4, observation.atr * 0.9, 8.0)
        bank_round_tolerance = max(bank_step * 0.16, bank_atr * 0.4, 5.0)
        if not self._nifty_higher_timeframe_allows(context, observation, option_type):
            return None

        if option_type == "CE":
            nifty_probe = min(candle.low for candle in nifty_recent)
            nifty_round = math.ceil(nifty_probe / nifty_step) * nifty_step
            nifty_distance = nifty_round - nifty_probe
            if nifty_distance < 0 or nifty_distance > nifty_near_tolerance:
                return None
            probe_index = max(index for index, candle in enumerate(nifty_recent) if candle.low == nifty_probe)
            trigger_reference = nifty_recent[probe_index:-1] or nifty_recent[:-1]
            bank_probe = min(candle.low for candle in bank_recent)
            bank_round = math.ceil(bank_probe / bank_step) * bank_step
            bank_swept = bank_probe <= bank_round - bank_round_tolerance * 0.25
            confirmed, confirmation_notes = self._companion_bank_confirmation(bank_recent, bank_round, option_type, bank_atr)
            if not (bank_swept and confirmed):
                return None
            trigger_price = max(nifty_round, max(candle.high for candle in trigger_reference))
            invalidation = min(nifty_probe, current.low) - observation.atr * 0.18
            target_spot = self.next_upside_target(context, current.close, max(observation.atr * 0.9, abs(current.close - invalidation), 1.0))
            first_target = current.close + max(observation.atr * 0.9, abs(current.close - invalidation), 1.0)
            ready_to_enter = current.close > trigger_price and current.close > current.open
            score = 64.0
            notes = [
                "Nifty front-ran a round-number sell-side sweep without fully tagging it.",
                "Bank Nifty completed the deeper sell-side sweep and reclaimed, so cross-index reversal context is valid.",
            ]
            notes.extend(confirmation_notes)
            rule_ids = ["R2", "R22", "R68", "R78", "R84", "R103", "R104"]
            event = SweepEvent(
                side="sell",
                level_label=f"Companion Round Number {nifty_round:.2f}",
                level_price=round(nifty_round, 2),
                sweep_index=max(len(nifty_session) - 2, 0),
                reclaim_index=len(nifty_session) - 1,
                trigger_index=len(nifty_session) - 1,
                sweep_price=round(nifty_probe, 2),
                defended_level=round(nifty_round, 2),
                trigger_price=round(trigger_price, 2),
                invalidation_level=round(invalidation, 2),
                primary=True,
                quality="tradable",
                notes=list(notes),
            )
        else:
            nifty_probe = max(candle.high for candle in nifty_recent)
            nifty_round = math.floor(nifty_probe / nifty_step) * nifty_step
            nifty_distance = nifty_probe - nifty_round
            if nifty_distance < 0 or nifty_distance > nifty_near_tolerance:
                return None
            probe_index = max(index for index, candle in enumerate(nifty_recent) if candle.high == nifty_probe)
            trigger_reference = nifty_recent[probe_index:-1] or nifty_recent[:-1]
            bank_probe = max(candle.high for candle in bank_recent)
            bank_round = math.floor(bank_probe / bank_step) * bank_step
            bank_swept = bank_probe >= bank_round + bank_round_tolerance * 0.25
            confirmed, confirmation_notes = self._companion_bank_confirmation(bank_recent, bank_round, option_type, bank_atr)
            if not (bank_swept and confirmed):
                return None
            trigger_price = min(nifty_round, min(candle.low for candle in trigger_reference))
            invalidation = max(nifty_probe, current.high) + observation.atr * 0.18
            target_spot = self.next_downside_target(context, current.close, max(observation.atr * 0.9, abs(invalidation - current.close), 1.0))
            first_target = current.close - max(observation.atr * 0.9, abs(invalidation - current.close), 1.0)
            ready_to_enter = current.close < trigger_price and current.close < current.open
            score = 64.0
            notes = [
                "Nifty front-ran a round-number buy-side sweep without fully tagging it.",
                "Bank Nifty completed the deeper buy-side sweep and rejected, so cross-index reversal context is valid.",
            ]
            notes.extend(confirmation_notes)
            rule_ids = ["R2", "R22", "R68", "R78", "R84", "R103", "R104"]
            event = SweepEvent(
                side="buy",
                level_label=f"Companion Round Number {nifty_round:.2f}",
                level_price=round(nifty_round, 2),
                sweep_index=max(len(nifty_session) - 2, 0),
                reclaim_index=len(nifty_session) - 1,
                trigger_index=len(nifty_session) - 1,
                sweep_price=round(nifty_probe, 2),
                defended_level=round(nifty_round, 2),
                trigger_price=round(trigger_price, 2),
                invalidation_level=round(invalidation, 2),
                primary=True,
                quality="tradable",
                notes=list(notes),
            )

        if observation.day_type in {"trap-day", "gap-reversal", "double-side-hunt"}:
            score += 8
            notes.append("Current Nifty day type supports reversal behavior.")
        if observation.two_sided_participation:
            score += 4
            notes.append("Two-sided participation strengthens the companion-led reversal read.")
        distance_adjustment, distance_note = self._nifty_round_distance_adjustment(
            observation,
            f"Round Number {nifty_round:.2f}",
            nifty_probe,
            nifty_round,
        )
        score += distance_adjustment
        if distance_note:
            notes.append(distance_note)
        htf_adjustment, htf_note = self._nifty_htf_score_adjustment(context, observation, option_type)
        score += htf_adjustment
        if htf_note:
            notes.append(htf_note)
        target_spot, target_adjusted = self._enforce_nifty_three_r_target(
            context,
            option_type=option_type,
            entry_price=current.close,
            target_spot=target_spot,
            risk=max(abs(current.close - invalidation), observation.atr * 0.2, 0.01),
        )
        if target_adjusted:
            notes.append("Nifty target is normalized to at least 3R from the entry/invalidation risk.")
            rule_ids.append("R142")
        if ready_to_enter:
            score += 10
            notes.append("Nifty confirmed the companion reversal with its own trigger close.")
        else:
            score -= 6
            notes.append("Nifty is still near the level but has not confirmed the trigger close yet.")

        return SetupCandidate(
            setup_type="companion_round_reclaim_long" if option_type == "CE" else "companion_round_rejection_short",
            direction=direction,
            option_type=option_type,
            trigger_basis="close_above" if option_type == "CE" else "close_below",
            trigger_price=round(trigger_price, 2),
            invalidation_level=round(invalidation, 2),
            defended_level=round(nifty_round, 2),
            target_spot_price=round(target_spot, 2),
            first_target_price=round(first_target, 2),
            score=max(0.0, min(score, 100.0)),
            ready_to_enter=ready_to_enter,
            notes=notes,
            rule_ids=rule_ids,
            event=event,
        )

    def build_previous_close_candidates(self, context: StrategyContext, observation: Observation) -> list[SetupCandidate]:
        previous_close = context.previous_day.close
        if not previous_close or not observation.previous_close_touched:
            return []

        session = context.session_candles
        current = context.current_candle
        tolerance = max(observation.atr * 0.08, 0.15)
        distance_from_previous_close = abs(current.close - previous_close)
        anti_chase_distance = max(observation.atr * 1.1, tolerance * 5)
        midday_previous_close_gate = (
            observation.session_phase == "midday"
            and observation.participation_state in {"fair_value_churn", "post_trend_balance"}
        )
        if midday_previous_close_gate or distance_from_previous_close > anti_chase_distance:
            return []
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
            score = self.apply_regime_filter(score, notes, rule_ids, observation)
            trigger_reference = recent_three[:-1] if len(recent_three) > 1 else recent_three
            trigger_price = max(candle.high for candle in trigger_reference)
            ready_to_enter = current.close > trigger_price and current.close > current.open
            if ready_to_enter:
                score += 10
            target_spot = self.next_upside_target(context, current.close, risk)
            target_spot, target_adjusted = self._enforce_nifty_three_r_target(
                context,
                option_type="CE",
                entry_price=current.close,
                target_spot=target_spot,
                risk=max(current.close - (previous_close - observation.atr * 0.25), observation.atr * 0.2, 0.01),
            )
            if target_adjusted:
                notes.append("Nifty target is normalized to at least 3R from the entry/invalidation risk.")
                rule_ids.append("R142")
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
            score = self.apply_regime_filter(score, notes, rule_ids, observation)
            trigger_reference = recent_three[:-1] if len(recent_three) > 1 else recent_three
            trigger_price = min(candle.low for candle in trigger_reference)
            ready_to_enter = current.close < trigger_price and current.close < current.open
            if ready_to_enter:
                score += 10
            target_spot = self.next_downside_target(context, current.close, risk)
            target_spot, target_adjusted = self._enforce_nifty_three_r_target(
                context,
                option_type="PE",
                entry_price=current.close,
                target_spot=target_spot,
                risk=max((previous_close + observation.atr * 0.25) - current.close, observation.atr * 0.2, 0.01),
            )
            if target_adjusted:
                notes.append("Nifty target is normalized to at least 3R from the entry/invalidation risk.")
                rule_ids.append("R142")
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
        if not self._nifty_higher_timeframe_allows(context, observation, option_type):
            return None
        current_strength = self.candle_strength(current)
        reclaim_strength = self.candle_strength(reclaim_candle)
        nifty_immediate_reclaim = self._nifty_immediate_sweep_reclaim(
            context,
            event,
            observation,
            option_type=option_type,
            current=current,
            reclaim_strength=reclaim_strength,
        )
        continuation_count = 0
        hold_count = 0
        follow_through = False
        directional_extension_r = (
            (current.close - session[0].open) / max(observation.atr, 0.01)
            if option_type == "CE"
            else (session[0].open - current.close) / max(observation.atr, 0.01)
        )
        value_extension_r = (
            (current.close - observation.vwap) / max(observation.atr, 0.01)
            if option_type == "CE"
            else (observation.vwap - current.close) / max(observation.atr, 0.01)
        )

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
        if "pivot " in level_label or level_label == "pivot point":
            score += 7
            notes.append("Classic pivot-point liquidity was tested and then rejected or reclaimed.")
            rule_ids.extend(["R2", "R72", "R73", "R79"])
        if "equal high cluster" in level_label or "equal low cluster" in level_label:
            score += 8
            notes.append("Equal-high or equal-low stop cluster adds stronger trap potential.")
            rule_ids.extend(["R3", "R25", "R26", "R52", "R77"])
        if "same-day swing high" in level_label or "same-day swing low" in level_label:
            score += 5
            notes.append("Same-day swing liquidity map aligns with the sweep location.")
            rule_ids.extend(["R2", "R76", "R79"])
        if "previous-day swing high" in level_label or "previous-day swing low" in level_label:
            score += 6
            notes.append("Previous-day structural swing liquidity aligns with the sweep location.")
            rule_ids.extend(["R3", "R15", "R72", "R79", "R84"])
        if "previous-day resistance shelf" in level_label or "previous-day support shelf" in level_label:
            score += 8
            notes.append("Repeated prior-day rejection shelf concentrates obvious support or resistance liquidity here.")
            rule_ids.extend(["R3", "R15", "R72", "R73", "R79", "R84"])
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
        if any("pivot " in label or label == "pivot point" for label in confluence_labels):
            score += 5
            notes.append("Pivot-point structure also overlaps the sweep zone.")
            rule_ids.extend(["R2", "R72", "R73", "R79"])
        if any("equal high cluster" in label or "equal low cluster" in label for label in confluence_labels):
            score += 6
            notes.append("Clustered equal-high or equal-low liquidity reinforces stop concentration here.")
            rule_ids.extend(["R3", "R25", "R26", "R77"])
        if any("same-day swing high" in label or "same-day swing low" in label for label in confluence_labels):
            score += 4
            notes.append("Nearby same-day swing liquidity adds extra confluence.")
            rule_ids.extend(["R2", "R76", "R79"])
        if any("previous-day swing high" in label or "previous-day swing low" in label for label in confluence_labels):
            score += 4
            notes.append("Prior-day swing structure adds confluence to the trap location.")
            rule_ids.extend(["R3", "R15", "R72", "R79", "R84"])
        if any("previous-day resistance shelf" in label or "previous-day support shelf" in label for label in confluence_labels):
            score += 5
            notes.append("Repeated prior-day shelf behavior reinforces the defended zone.")
            rule_ids.extend(["R3", "R15", "R72", "R73", "R79", "R84"])
        distance_adjustment, distance_note = self._nifty_round_distance_adjustment(
            observation,
            event.level_label,
            event.sweep_price,
            event.level_price,
        )
        score += distance_adjustment
        if distance_note:
            notes.append(distance_note)
        setup_type = self._classify_reclaim_setup_type(
            option_type=option_type,
            event=event,
            confluence_labels=confluence_labels,
            session_length=len(session),
        )
        has_fresh_fuel = event.primary or self._is_obvious_stop_pool_label(event.level_label) or any(
            self._is_obvious_stop_pool_label(label) for label in confluence_labels
        )
        if not has_fresh_fuel and level_label.startswith(("same-day swing high", "same-day swing low")):
            return None
        first_candle_range_r, gap_r, opening_shock = self._opening_shock_metrics(session, observation)
        if (
            observation.large_gap_reset
            and opening_shock
            and self._is_gap_reset_trap_direction(observation, option_type)
        ):
            if len(session) <= 20:
                return None
            if len(session) <= 30 and not (event.primary and hold_count >= 1 and follow_through):
                return None
        if (
            option_type == "PE"
            and observation.gap < -max(observation.atr * 0.25, 0.2)
            and observation.value_state == "discount"
            and observation.session_phase in {"opening-map", "primary-trap-window"}
        ):
            recovered_into_major_upper_reference = self._is_major_upper_reference_label(event.level_label) and (
                event.level_price >= max(observation.opening_range_high, context.previous_day.close)
                or event.level_price >= context.previous_day.close - observation.atr * 0.2
            )
            if not (recovered_into_major_upper_reference and follow_through):
                return None
        gap_down_recovery_morning = (
            observation.gap < -max(observation.atr * 0.25, 0.2)
            and observation.session_phase in {"opening-map", "primary-trap-window"}
        )
        if option_type == "PE" and gap_down_recovery_morning:
            recovery_has_lifted = current.close > session[0].close + observation.atr * 0.35
            if recovery_has_lifted:
                recovered_into_major_upper_reference = self._is_major_upper_reference_label(event.level_label) and (
                    event.level_price >= max(observation.opening_range_high, context.previous_day.close, observation.vwap)
                    or event.level_price >= observation.vwap - observation.atr * 0.15
                )
                bearish_acceptance = current.close < min(context.previous_day.close, observation.vwap)
                if not (recovered_into_major_upper_reference and follow_through and bearish_acceptance):
                    return None
        if option_type == "CE" and gap_down_recovery_morning:
            recovery_burst_is_extended = current.close > max(context.previous_day.close, observation.vwap) + observation.atr * 0.35
            early_retest_hold = hold_count >= 1 and current.low <= reclaim_candle.high + observation.atr * 0.15
            if recovery_burst_is_extended and not early_retest_hold:
                return None
        if directional_extension_r >= 4.2 and value_extension_r >= 2.6 and not event.primary:
            return None
        strong_expansion_leg = directional_extension_r >= 3.2 and value_extension_r >= 1.8 and observation.strong_intent
        if reclaim_index is not None:
            score += 15
        if "reclaimed back" in " ".join(event.notes).lower():
            rule_ids.extend(["R31", "R32"])
        if reclaim_strength >= 0.55:
            score += 10
            notes.append("Reclaim or rejection candle has healthy body quality.")
            rule_ids.append("R89")
        elif nifty_immediate_reclaim:
            score += 4
            notes.append("Same-candle sweep reclaim is accepted even without a large body because the liquidity trap closed back inside immediately.")
            rule_ids.extend(["R140", "R141"])
        else:
            score -= 12
            notes.append("Reclaim or rejection candle is weak.")
        if follow_through:
            score += 12
            notes.append("Follow-through confirmed after the reclaim or rejection.")
            rule_ids.append("R89")
        elif nifty_immediate_reclaim:
            score += 8
            notes.append("Nifty primary sweep reclaimed on the current candle, so entry does not wait for a later follow-through candle.")
            rule_ids.extend(["R140", "R141"])
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
        if (
            observation.large_gap_reset
            and opening_shock
            and self._is_gap_reset_trap_direction(observation, option_type)
            and len(session) <= 30
        ):
            score -= 18
            notes.append("Opening gap reset was extreme, so only a deeper reclaim with retest-hold can stay tradable.")
            rule_ids.extend(["R17", "R47", "R64", "R80", "R89"])
        if opening_shock and first_candle_range_r >= 5.5 and gap_r >= 4.0 and continuation_count == 0:
            score -= 12
            notes.append("The first candle shock was outsized, so immediate reclaim readings are less trustworthy.")
            rule_ids.extend(["R17", "R47", "R64"])
        if observation.session_phase in {"discovery", "opening-map"} and continuation_count == 0:
            score -= 10
            notes.append("Still too early to force confirmation from opening noise.")
            rule_ids.extend(["R60", "R64"])
        if observation.expiry_session and event.quality == "weak":
            score -= 8
            notes.append("Expiry-day conditions demand stronger confirmation than this weak trap.")
            rule_ids.append("R47")
        if current_strength < 0.35 and not nifty_immediate_reclaim:
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
        htf_adjustment, htf_note = self._nifty_htf_score_adjustment(context, observation, option_type)
        score += htf_adjustment
        if htf_note:
            notes.append(htf_note)
        ledger_adjustment, ledger_note, ledger_rules = self._liquidity_ledger_score_adjustment(
            observation,
            option_type=option_type,
        )
        score += ledger_adjustment
        if ledger_note:
            notes.append(ledger_note)
            rule_ids.extend(ledger_rules)
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
        if setup_type in {"bullish_pullback_continuation", "bearish_pullback_continuation"}:
            score -= 20
            notes.append("This is a trend pullback continuation, so the location and room standards are stricter than a fresh trap reclaim.")
            rule_ids.extend(["R75", "R90", "R97", "R100"])
            if not follow_through:
                score -= 10
                notes.append("Continuation pullback still lacks the follow-through needed for a mature-trend entry.")
            if hold_count < 1:
                score -= 8
                notes.append("Continuation pullback has not shown a clean defended retest yet.")
            if directional_extension_r >= 3.2:
                score -= 16
                notes.append("Session has already traveled a long distance from the open, so continuation risk is elevated.")
                rule_ids.extend(["R51", "R75", "R101"])
            if value_extension_r >= 2.1:
                score -= 12
                notes.append("Price is already stretched away from VWAP, so this continuation entry risks chasing extension.")
                rule_ids.extend(["R20", "R22", "R51"])
        if option_type == "CE" and strong_expansion_leg:
            score -= 10
            notes.append("One strong bullish expansion leg is already mature, so repeated same-direction reclaims deserve extra skepticism.")
            rule_ids.extend(["R51", "R75", "R90", "R101"])
        if option_type == "PE" and strong_expansion_leg:
            score -= 10
            notes.append("One strong bearish expansion leg is already mature, so repeated same-direction rejections deserve extra skepticism.")
            rule_ids.extend(["R51", "R75", "R90", "R101"])
        if event.primary and directional_extension_r >= 4.2 and value_extension_r >= 2.6:
            score -= 14
            notes.append("The move is already extended, so only this fresh primary sweep keeps the setup alive.")
            rule_ids.extend(["R51", "R75", "R101"])
        if option_type == "CE" and directional_extension_r >= 4.5 and value_extension_r >= 2.2:
            score -= 14
            notes.append("Same-day bullish extension is already stretched far from both open and VWAP, so exhaustion risk is elevated.")
            rule_ids.extend(["R20", "R51", "R75", "R101"])
        if option_type == "PE" and directional_extension_r >= 4.5 and value_extension_r >= 2.2:
            score -= 14
            notes.append("Same-day bearish extension is already stretched far from both open and VWAP, so exhaustion risk is elevated.")
            rule_ids.extend(["R20", "R51", "R75", "R101"])
        score = self.apply_regime_filter(score, notes, rule_ids, observation)

        risk = max(abs(reclaim_candle.close - event.trigger_price), observation.atr * 0.8)
        if option_type == "CE":
            target_spot = self.next_upside_target(context, max(current.close, reclaim_candle.close), risk)
            first_target = current.close + risk
            retest_hold = hold_count >= 1 and current.close > event.defended_level and current.low <= reclaim_candle.high + observation.atr * 0.15
            stock_retracement_confirmed = self._stock_retracement_reclaim_confirmed(
                context,
                observation,
                event,
                option_type=option_type,
                reclaim_candle=reclaim_candle,
                current=current,
            )
            stock_early_entry = self._stock_early_retest_entry(
                context,
                observation,
                event,
                option_type=option_type,
                setup_type=setup_type,
                current=current,
                reclaim_candle=reclaim_candle,
                current_strength=current_strength,
                reclaim_strength=reclaim_strength,
                hold_count=hold_count,
                continuation_count=continuation_count,
            )
            stock_opening_retest_required = (
                not context.instrument.supports_options
                and setup_type in {"bullish_reclaim_watch", "bearish_rejection_watch"}
                and observation.session_phase in {"opening-map", "primary-trap-window"}
            )
            ready_to_enter = (
                current.close > reclaim_candle.high
                and current.close > event.defended_level
                and current.close > current.open
                and (not stock_opening_retest_required or stock_retracement_confirmed)
            ) or (
                retest_hold
                and follow_through
                and (not stock_opening_retest_required or stock_retracement_confirmed)
            ) or stock_early_entry or nifty_immediate_reclaim
            trigger_basis = "close_above"
            trigger_price = (
                round(max(event.defended_level, current.low), 2)
                if stock_early_entry or nifty_immediate_reclaim
                else max(reclaim_candle.high, event.defended_level + observation.atr * 0.1)
            )
            invalidation = min(event.trigger_price, event.defended_level - observation.atr * 0.18)
            plan_risk = max(current.close - invalidation, observation.atr * 0.2, 0.01)
            target_spot, target_adjusted = self._enforce_nifty_three_r_target(
                context,
                option_type=option_type,
                entry_price=current.close,
                target_spot=target_spot,
                risk=plan_risk,
            )
        else:
            target_spot = self.next_downside_target(context, min(current.close, reclaim_candle.close), risk)
            first_target = current.close - risk
            retest_hold = hold_count >= 1 and current.close < event.defended_level and current.high >= reclaim_candle.low - observation.atr * 0.15
            stock_retracement_confirmed = self._stock_retracement_reclaim_confirmed(
                context,
                observation,
                event,
                option_type=option_type,
                reclaim_candle=reclaim_candle,
                current=current,
            )
            stock_early_entry = self._stock_early_retest_entry(
                context,
                observation,
                event,
                option_type=option_type,
                setup_type=setup_type,
                current=current,
                reclaim_candle=reclaim_candle,
                current_strength=current_strength,
                reclaim_strength=reclaim_strength,
                hold_count=hold_count,
                continuation_count=continuation_count,
            )
            stock_opening_retest_required = (
                not context.instrument.supports_options
                and setup_type in {"bullish_reclaim_watch", "bearish_rejection_watch"}
                and observation.session_phase in {"opening-map", "primary-trap-window"}
            )
            ready_to_enter = (
                current.close < reclaim_candle.low
                and current.close < event.defended_level
                and current.close < current.open
                and (not stock_opening_retest_required or stock_retracement_confirmed)
            ) or (
                retest_hold
                and follow_through
                and (not stock_opening_retest_required or stock_retracement_confirmed)
            ) or stock_early_entry or nifty_immediate_reclaim
            trigger_basis = "close_below"
            trigger_price = (
                round(min(event.defended_level, current.high), 2)
                if stock_early_entry or nifty_immediate_reclaim
                else min(reclaim_candle.low, event.defended_level - observation.atr * 0.1)
            )
            invalidation = max(event.trigger_price, event.defended_level + observation.atr * 0.18)
            plan_risk = max(invalidation - current.close, observation.atr * 0.2, 0.01)
            target_spot, target_adjusted = self._enforce_nifty_three_r_target(
                context,
                option_type=option_type,
                entry_price=current.close,
                target_spot=target_spot,
                risk=plan_risk,
            )
        if target_adjusted:
            notes.append("Nifty target is normalized to at least 3R from the entry/invalidation risk.")
            rule_ids.append("R142")
        if stock_early_entry:
            score += 12
            notes.append(
                "Stock mode allows the first shallow defended retest after a primary sweep, so entry can trigger before a mature multi-candle rally."
            )
            rule_ids.extend(["R105", "R106"])
        if nifty_immediate_reclaim:
            score += 8
        elif (
            not context.instrument.supports_options
            and setup_type in {"bullish_reclaim_watch", "bearish_rejection_watch"}
            and observation.session_phase in {"opening-map", "primary-trap-window"}
            and not stock_retracement_confirmed
        ):
            score -= 12
            notes.append("Stock mode now waits for an actual retracement and reclaim before entering during the opening phase.")
            rule_ids.extend(["R63", "R69", "R79", "R100"])
        if ready_to_enter and hold_count >= 1 and continuation_count == 0:
            score += 4
            notes.append("Retest held after the break, so continuation entry is acceptable without chasing the breakout candle.")
            rule_ids.extend(["R63", "R100"])
        retest_adjustment, retest_note = self._nifty_retest_quality_adjustment(
            context,
            observation,
            event,
            option_type,
            reclaim_candle,
        )
        score += retest_adjustment
        if retest_note:
            notes.append(retest_note)

        room = abs(target_spot - current.close)
        if setup_type in {"bullish_pullback_continuation", "bearish_pullback_continuation"} and room < risk * 2.2:
            score -= 14
            notes.append("Continuation pullback does not have enough room left to the next liquidity pool.")
            rule_ids.extend(["R44", "R55", "R98", "R100"])
        if room >= risk * 1.8:
            score += 10
            notes.append("There is enough room to the next opposing liquidity.")
            rule_ids.extend(["R44", "R98"])
        else:
            score -= 10
            notes.append("Reward-to-risk is weak to the next liquidity.")

        return SetupCandidate(
            setup_type=setup_type,
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

        pending_setup_max_bars = self._pending_setup_max_bars_for_context(context)
        if bars_open > pending_setup_max_bars:
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
        bullish_trade = trade.direction in {"LONG_CALL", "LONG_STOCK", "SHORT_PUT"}
        stock_mode_trade = not context.instrument.supports_options and trade.price_mode == "cash"
        instrument_mode = getattr(context.instrument.mode, "value", context.instrument.mode)
        nifty_mode_trade = (
            instrument_mode == "nifty"
            and context.instrument.symbol == "NIFTY"
            and context.instrument.supports_options
        )
        heuristic_early_exit_enabled = (
            (stock_mode_trade and context.stock_heuristic_early_exit_enabled)
            or (nifty_mode_trade and context.nifty_heuristic_early_exit_enabled)
            or ((not stock_mode_trade) and (not nifty_mode_trade))
        )
        progress_r = (
            (current_spot - trade.entry_spot_price) / max(risk, 0.01)
            if bullish_trade
            else (trade.entry_spot_price - current_spot) / max(risk, 0.01)
        )
        trailing_stop_enabled = (
            (stock_mode_trade and context.stock_trailing_stop_enabled)
            or (nifty_mode_trade and context.nifty_trailing_stop_enabled)
        )
        bars_since_entry = sum(1 for candle in context.session_candles if candle.timestamp >= trade.entry_time)
        setup_is_previous_close = trade.setup_type in {"previous_close_reclaim_long", "previous_close_rejection_short"}
        regime_deteriorated = observation.participation_state in {"fair_value_churn", "post_trend_balance"} and (
            observation.session_phase == "midday" or observation.range_state == "compressing"
        )

        if nifty_mode_trade and context.nifty_target_enabled and context.nifty_target_points > 0:
            target_spot = (
                trade.entry_spot_price + context.nifty_target_points
                if bullish_trade
                else trade.entry_spot_price - context.nifty_target_points
            )
            target_tagged = (
                context.current_candle.high >= target_spot
                if bullish_trade
                else context.current_candle.low <= target_spot
            )
            if target_tagged:
                return TradeDecision(
                    action=TradeAction.exit,
                    confidence=0.95,
                    reason=(
                        f"Nifty fixed target control booked profit because spot reached "
                        f"{target_spot:.2f}, {context.nifty_target_points:.2f} points from entry."
                    ),
                    decision_source="heuristic",
                    option_type=trade.option_type,
                    target_spot_price=round(target_spot, 2),
                    market_state=observation.day_type,
                    setup_type=trade.setup_type,
                    rule_ids_used=["R41", "R42", "R44", "R74", "R99"],
                )

        if trade.invalidation_level is not None:
            invalidation_tagged = (
                context.current_candle.low <= trade.invalidation_level
                if bullish_trade
                else context.current_candle.high >= trade.invalidation_level
            )
            if nifty_mode_trade and invalidation_tagged:
                return TradeDecision(
                    action=TradeAction.exit,
                    confidence=0.92,
                    reason=(
                        f"Nifty spot touched invalidation {trade.invalidation_level:.2f}, "
                        "so exit without waiting for candle close."
                    ),
                    decision_source="heuristic",
                    option_type=trade.option_type,
                    market_state=observation.day_type,
                    setup_type=trade.setup_type,
                    rule_ids_used=["R41", "R42", "R45", "R66", "R99"],
                )

        if nifty_mode_trade and context.nifty_cost_sl_enabled and context.nifty_cost_sl_points > 0:
            favorable_level = (
                trade.entry_spot_price + context.nifty_cost_sl_points
                if bullish_trade
                else trade.entry_spot_price - context.nifty_cost_sl_points
            )
            favorable_tagged = (
                context.current_candle.high >= favorable_level
                if bullish_trade
                else context.current_candle.low <= favorable_level
            )
            already_at_cost = (
                trade.invalidation_level is not None
                and (
                    (bullish_trade and trade.invalidation_level >= trade.entry_spot_price)
                    or ((not bullish_trade) and trade.invalidation_level <= trade.entry_spot_price)
                )
            )
            if favorable_tagged and not already_at_cost:
                return TradeDecision(
                    action=TradeAction.update_stop,
                    confidence=0.86,
                    reason=(
                        f"Nifty cost-SL control is active: spot moved {context.nifty_cost_sl_points:.2f} "
                        "points in favor, so move invalidation to entry cost."
                    ),
                    decision_source="heuristic",
                    option_type=trade.option_type,
                    invalidation_level=round(trade.entry_spot_price, 2),
                    market_state=observation.day_type,
                    setup_type=trade.setup_type,
                    rule_ids_used=["R41", "R42", "R43", "R74", "R99"],
                )

        if heuristic_early_exit_enabled and regime_deteriorated and bars_since_entry >= 5 and progress_r <= 0.25 and (
            setup_is_previous_close or observation.range_state == "compressing"
        ):
            return TradeDecision(
                action=TradeAction.exit,
                confidence=0.8,
                reason=(
                    "Trade is no longer behaving cleanly and the regime has deteriorated into balance or churn, "
                    "so exit before the thesis decays further."
                ),
                decision_source="heuristic",
                option_type=trade.option_type,
                market_state=observation.day_type,
                setup_type=trade.setup_type,
                rule_ids_used=["R41", "R42", "R45", "R55", "R74", "R90", "R91", "R99"],
            )

        candidates = self.build_candidates(context, observation)
        strongest_opposite = self.select_best_candidate(
            [candidate for candidate in candidates if (candidate.option_type == "PE") == bullish_trade]
        )
        if heuristic_early_exit_enabled and strongest_opposite and strongest_opposite.ready_to_enter and strongest_opposite.score >= 78:
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

        if (
            stock_mode_trade
            and context.stock_partial_profit_enabled
            and trade.first_target_price is not None
            and trade.partial_exit_count == 0
        ):
            first_target_tagged = (
                context.current_candle.high >= trade.first_target_price
                if bullish_trade
                else context.current_candle.low <= trade.first_target_price
            )
            if first_target_tagged:
                partial_exit_quantity = max(
                    1,
                    min(
                        trade.open_quantity if trade.open_quantity is not None else trade.quantity,
                        max(1, (trade.open_quantity if trade.open_quantity is not None else trade.quantity) // 2),
                    ),
                )
                return TradeDecision(
                    action=TradeAction.partial_exit,
                    confidence=0.84,
                    reason=(
                        "First stock target was tagged intrabar, so book partial profits early and reduce exposure "
                        "before the trend retracement deepens."
                    ),
                    decision_source="heuristic",
                    option_type=trade.option_type,
                    partial_exit_quantity=partial_exit_quantity,
                    market_state=observation.day_type,
                    setup_type=trade.setup_type,
                    rule_ids_used=["R41", "R42", "R46", "R74", "R99", "R113"],
                )

        if stock_mode_trade and context.stock_trailing_stop_enabled and trade.partial_exit_count > 0:
            latest_defense = self.latest_defended_zone(context, bullish_trade, observation)
            if bullish_trade:
                new_invalidation = max(trade.invalidation_level or trade.entry_spot_price, trade.entry_spot_price, latest_defense)
            else:
                new_invalidation = min(trade.invalidation_level or trade.entry_spot_price, trade.entry_spot_price, latest_defense)
            if trade.invalidation_level is None or abs(new_invalidation - trade.invalidation_level) >= 0.05:
                return TradeDecision(
                    action=TradeAction.update_stop,
                    confidence=0.82,
                    reason=(
                        "Partial profits are booked on the stock trade, so tighten the stop aggressively toward "
                        "breakeven or the newest defended zone."
                    ),
                    decision_source="heuristic",
                    option_type=trade.option_type,
                    invalidation_level=round(new_invalidation, 2),
                    market_state=observation.day_type,
                    setup_type=trade.setup_type,
                    rule_ids_used=["R41", "R42", "R43", "R46", "R74", "R99", "R113"],
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

        if context.intelligent_pyramiding_enabled:
            breached_legs = [
                leg
                for leg in trade.pyramid_legs
                if leg.status == "OPEN"
                and leg.open_quantity > 0
                and (
                    (bullish_trade and current_spot <= leg.invalidation_level)
                    or ((not bullish_trade) and current_spot >= leg.invalidation_level)
                )
            ]
            if breached_legs:
                exit_quantity = sum(max(int(leg.open_quantity or 0), 0) for leg in breached_legs)
                return TradeDecision(
                    action=TradeAction.exit_pyramid_leg,
                    confidence=0.88,
                    reason=(
                        "Intelligent pyramiding add-stop was hit, so remove only the failed add leg quantity "
                        "and keep the original thesis alive until the main invalidation breaks."
                    ),
                    decision_source="heuristic",
                    option_type=trade.option_type,
                    partial_exit_quantity=exit_quantity,
                    pyramid_leg_ids=[leg.leg_id for leg in breached_legs],
                    market_state=observation.day_type,
                    setup_type=trade.setup_type,
                    rule_ids_used=["R41", "R42", "R45", "R66", "R99"],
                )

        pyramid_count = max(int(trade.pyramid_count or 0), 0)
        if context.intelligent_pyramiding_enabled and pyramid_count < 2:
            base_quantity = max(int(trade.base_quantity or trade.quantity or 1), 1)
            same_side = self.select_best_candidate(
                [candidate for candidate in candidates if (candidate.option_type == "CE") == bullish_trade]
            )
            if same_side is not None:
                enter_threshold, _, allow_only_exceptional = self._effective_entry_thresholds(context, observation, same_side)
                proposed_stop = same_side.invalidation_level
                target_room = (
                    same_side.target_spot_price - current_spot
                    if bullish_trade
                    else current_spot - same_side.target_spot_price
                )
                initial_unit_risk = max(
                    (
                        trade.entry_spot_price - (trade.invalidation_level or trade.entry_spot_price)
                        if bullish_trade
                        else (trade.invalidation_level or trade.entry_spot_price) - trade.entry_spot_price
                    ),
                    observation.atr * 0.6,
                    0.01,
                )
                add_unit_risk = (
                    current_spot - proposed_stop
                    if bullish_trade
                    else proposed_stop - current_spot
                )
                main_open_quantity = max(
                    (trade.open_quantity if trade.open_quantity is not None else trade.quantity)
                    - sum(max(int(leg.open_quantity or 0), 0) for leg in trade.pyramid_legs if leg.status == "OPEN"),
                    0,
                )
                main_risk = initial_unit_risk * main_open_quantity
                existing_add_risk = sum(
                    max(
                        (
                            leg.entry_spot_price - leg.invalidation_level
                            if bullish_trade
                            else leg.invalidation_level - leg.entry_spot_price
                        ),
                        0.0,
                    )
                    * max(int(leg.open_quantity or 0), 0)
                    for leg in trade.pyramid_legs
                    if leg.status == "OPEN"
                )
                proposed_add_risk = max(add_unit_risk, 0.0) * base_quantity
                initial_budget = initial_unit_risk * max(int(trade.base_quantity or base_quantity), 1)
                risk_budget = max(initial_budget * 1.5, initial_budget + (observation.atr * base_quantity * 0.25))
                total_risk_after_add = main_risk + existing_add_risk + proposed_add_risk
                required_progress = 0.6 + (pyramid_count * 0.5)
                opposite_pressure = strongest_opposite.score if strongest_opposite is not None else 0.0
                already_added_this_candle = trade.last_pyramid_time == context.current_candle.timestamp
                late_session_exception = (
                    same_side.event.primary
                    and same_side.event.quality == "explosive"
                    and same_side.score >= enter_threshold + 5
                )
                if (
                    same_side.ready_to_enter
                    and same_side.score >= max(enter_threshold, 68.0)
                    and progress_r >= required_progress
                    and add_unit_risk > 0
                    and add_unit_risk <= initial_unit_risk * 0.8
                    and target_room >= add_unit_risk * 2.0
                    and total_risk_after_add <= risk_budget
                    and opposite_pressure < 72
                    and not already_added_this_candle
                    and (not allow_only_exceptional or late_session_exception)
                ):
                    return TradeDecision(
                        action=TradeAction.add_position,
                        confidence=min(0.92, same_side.score / 100),
                        reason=(
                            f"Intelligent pyramiding add {pyramid_count + 1}/2 is allowed because the add has its "
                            f"own structural stop at {proposed_stop:.2f}, add risk is {add_unit_risk:.2f} per unit, "
                            f"and total open risk after adding stays inside the budget "
                            f"{total_risk_after_add:.2f}/{risk_budget:.2f}."
                        ),
                        decision_source="heuristic",
                        option_type=trade.option_type,
                        add_quantity=base_quantity,
                        invalidation_level=round(proposed_stop, 2),
                        target_spot_price=round(same_side.target_spot_price, 2),
                        market_state=observation.day_type,
                        setup_score=round(same_side.score, 2),
                        setup_type=same_side.setup_type,
                        rule_ids_used=list(dict.fromkeys(same_side.rule_ids + ["R41", "R42", "R63", "R74", "R98", "R99"])),
                    )

        elif context.pyramiding_enabled and pyramid_count < 2:
            base_quantity = max(int(trade.base_quantity or trade.quantity or 1), 1)
            required_progress = 1.0 + (pyramid_count * 0.6)
            stop_is_protected = (
                trade.invalidation_level is not None
                and (
                    (bullish_trade and trade.invalidation_level >= trade.entry_spot_price)
                    or ((not bullish_trade) and trade.invalidation_level <= trade.entry_spot_price)
                )
            )
            same_side = self.select_best_candidate(
                [candidate for candidate in candidates if (candidate.option_type == "CE") == bullish_trade]
            )
            if same_side is not None:
                enter_threshold, _, allow_only_exceptional = self._effective_entry_thresholds(context, observation, same_side)
                target_room = (
                    same_side.target_spot_price - current_spot
                    if bullish_trade
                    else current_spot - same_side.target_spot_price
                )
                late_session_exception = (
                    same_side.event.primary
                    and same_side.event.quality == "explosive"
                    and same_side.score >= enter_threshold + 5
                )
                opposite_pressure = strongest_opposite.score if strongest_opposite is not None else 0.0
                already_added_this_candle = trade.last_pyramid_time == context.current_candle.timestamp
                if (
                    same_side.ready_to_enter
                    and same_side.score >= max(enter_threshold, 68.0)
                    and progress_r >= required_progress
                    and stop_is_protected
                    and target_room >= risk * 1.2
                    and opposite_pressure < 72
                    and not already_added_this_candle
                    and (not allow_only_exceptional or late_session_exception)
                ):
                    return TradeDecision(
                        action=TradeAction.add_position,
                        confidence=min(0.92, same_side.score / 100),
                        reason=(
                            f"Pyramiding add {pyramid_count + 1}/2 is allowed because the active SL-hunting thesis "
                            f"is protected at breakeven or better, progress is {progress_r:.2f}R, and a same-side "
                            f"{same_side.setup_type} confirmed with score {same_side.score:.1f}/100. Add quantity "
                            f"matches the initial entry size."
                        ),
                        decision_source="heuristic",
                        option_type=trade.option_type,
                        add_quantity=base_quantity,
                        invalidation_level=round(trade.invalidation_level, 2) if trade.invalidation_level is not None else None,
                        target_spot_price=round(same_side.target_spot_price, 2),
                        market_state=observation.day_type,
                        setup_score=round(same_side.score, 2),
                        setup_type=same_side.setup_type,
                        rule_ids_used=list(dict.fromkeys(same_side.rule_ids + ["R41", "R42", "R43", "R63", "R74", "R99"])),
                    )

        if trailing_stop_enabled and progress_r >= 1.0:
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
            f"score {candidate.score:.1f}/100. Regime reads {observation.range_state} / "
            f"{observation.participation_state}. {joined_notes}"
        )

    def _append_candle_ref(
        self,
        refs_by_index: dict[int, dict],
        session: list[Candle],
        index: int | None,
        label: str,
    ) -> None:
        if index is None or index < 0 or index >= len(session):
            return
        existing = refs_by_index.get(index)
        if existing is not None:
            labels = existing["_labels"]
            if label not in labels:
                labels.append(label)
                existing["label"] = " / ".join(labels)
            return
        refs_by_index[index] = {
            "label": label,
            "_labels": [label],
            "index": index,
            "candle": session[index],
        }

    def _event_candle_refs(
        self,
        context: StrategyContext,
        event: SweepEvent | None,
        *,
        include_decision_candle: bool = True,
    ) -> list[dict]:
        session = context.session_candles
        refs_by_index: dict[int, dict] = {}
        if include_decision_candle and session:
            self._append_candle_ref(refs_by_index, session, len(session) - 1, "Decision candle")
        if event is not None:
            self._append_candle_ref(refs_by_index, session, event.sweep_index, "Sweep candle")
            self._append_candle_ref(refs_by_index, session, event.reclaim_index, "Reclaim candle")
            self._append_candle_ref(refs_by_index, session, event.trigger_index, "Trigger candle")
        ordered = sorted(refs_by_index.values(), key=lambda item: item["index"])
        for ref in ordered:
            ref.pop("_labels", None)
        return ordered

    def _matched_event(
        self,
        context: StrategyContext,
        best: SetupCandidate | None,
    ) -> tuple[str | None, float | None, list[dict]]:
        event = best.event if best is not None else None
        matched_level_label = event.level_label if event is not None else None
        matched_level_price = round(event.level_price, 2) if event is not None else None
        candle_refs = self._event_candle_refs(context, event)
        return matched_level_label, matched_level_price, candle_refs

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
        matched_level_label, matched_level_price, candle_refs = self._matched_event(context, best)

        status = "informational"
        block_reason = None
        if decision.action in {TradeAction.enter_call, TradeAction.enter_put}:
            status = "trade_entered"
        elif decision.action in {TradeAction.exit, TradeAction.partial_exit, TradeAction.add_position, TradeAction.exit_pyramid_leg, TradeAction.update_stop, TradeAction.update_target, TradeAction.hold}:
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
        elif best.score < self._effective_entry_thresholds(context, observation, best)[1]:
            status = "failed_threshold"
            block_reason = (
                f"Best setup score {best.score:.1f} stayed below arm threshold {self._effective_entry_thresholds(context, observation, best)[1]:.1f}."
            )
        elif not best.ready_to_enter:
            status = "setup_identified_waiting_confirmation"
            block_reason = "Setup was identified but confirmation or trigger basis was not satisfied yet."
        else:
            status = "setup_identified_waiting_confirmation"
            block_reason = "Setup was tradable structurally but still below direct-entry confidence."

        is_important = (
            decision.pending_setup_action != "NONE"
            or decision.action in {TradeAction.enter_call, TradeAction.enter_put, TradeAction.exit, TradeAction.partial_exit, TradeAction.add_position, TradeAction.exit_pyramid_leg, TradeAction.update_stop, TradeAction.update_target}
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
                "matched_level_label": matched_level_label,
                "matched_level_price": matched_level_price,
                "candle_refs": candle_refs,
                "block_reason": block_reason,
                "detail": detail,
            }
        )

    def record_narrative(
        self,
        context: StrategyContext,
        observation: Observation,
        decision: TradeDecision,
        candidates: list[SetupCandidate] | None = None,
    ) -> None:
        current = context.current_candle
        candidates = candidates if candidates is not None else self.build_candidates(context, observation)
        best = self.select_best_candidate(candidates)
        best_matched_level_label, best_matched_level_price, best_candle_refs = self._matched_event(context, best)
        for event in observation.buy_sweeps[:2] + observation.sell_sweeps[:2]:
            if event.reclaim_index == len(context.session_candles) - 1:
                title = f"{event.level_label} {event.quality} {'buyer' if event.side == 'buy' else 'seller'} trap"
                detail = " ".join(event.notes[:3])
                matched_level_price = round(event.level_price, 2)
                self._push_narrative(
                    timestamp=current.timestamp,
                    event_type="major-sweep",
                    title=title,
                    direction="LONG_PUT" if event.side == "buy" else "LONG_CALL",
                    price=event.level_price,
                    status=event.quality,
                    matched_level_label=event.level_label,
                    matched_level_price=matched_level_price,
                    candle_refs=self._event_candle_refs(context, event),
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
                matched_level_label="Previous Day Close",
                matched_level_price=round(context.previous_day.close, 2),
                candle_refs=self._event_candle_refs(context, None),
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
                matched_level_label=best_matched_level_label,
                matched_level_price=best_matched_level_price,
                candle_refs=best_candle_refs,
                detail=decision.reason,
            )

        if decision.action in {TradeAction.enter_call, TradeAction.enter_put, TradeAction.partial_exit, TradeAction.add_position, TradeAction.exit_pyramid_leg, TradeAction.exit}:
            self._push_narrative(
                timestamp=current.timestamp,
                event_type="trade-action",
                title=f"Trade action {decision.action.value}",
                direction="LONG_CALL" if decision.action == TradeAction.enter_call else "LONG_PUT" if decision.action == TradeAction.enter_put else None,
                price=context.current_candle.close,
                status=decision.action.value.lower(),
                matched_level_label=best_matched_level_label,
                matched_level_price=best_matched_level_price,
                candle_refs=best_candle_refs,
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
        matched_level_label: str | None,
        matched_level_price: float | None,
        candle_refs: list[dict] | None,
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
                "matched_level_label": matched_level_label,
                "matched_level_price": matched_level_price,
                "candle_refs": list(candle_refs or []),
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
