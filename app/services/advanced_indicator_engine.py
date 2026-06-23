from __future__ import annotations

from dataclasses import dataclass
from datetime import time

from app.schemas import Candle, TradeAction, TradeDecision


@dataclass(frozen=True)
class AdvancedIndicatorState:
    gmma_gc: bool
    gmma_dc: bool
    gmma_bull_regime: bool
    gmma_bear_regime: bool
    obv_bull: bool
    obv_bear: bool
    obv_value: float
    obv_fast: float
    obv_slow: float


@dataclass(frozen=True)
class CarryForwardSignal:
    side: str
    timestamp_label: str
    close: float


class AdvancedIndicatorEngine:
    short_lengths = (3, 5, 8, 10, 12, 15)
    long_lengths = (30, 35, 40, 45, 50, 60)
    obv_fast_len = 5
    obv_medium_len = 9
    obv_slow_len = 14
    minimum_candles = 65
    carry_forward_start = time(15, 0)
    carry_forward_end = time(15, 30)
    carry_forward_max_session_candles = 20

    def decide(self, candles: list[Candle]) -> TradeDecision:
        if len(candles) < self.minimum_candles:
            return TradeDecision(
                action=TradeAction.no_trade,
                confidence=0.0,
                reason=(
                    f"Heuristic Advance mode is warming GMMA/OBV data "
                    f"({len(candles)}/{self.minimum_candles} candles)."
                ),
                decision_source="heuristic-advance",
                setup_type="advanced_warmup",
            )

        state = self.state(candles)
        latest = candles[-1]
        atr = self._atr(candles[-20:])
        if state.gmma_gc and state.gmma_bull_regime and state.obv_bull:
            invalidation = self._defended_level(candles, bullish=True, atr=atr)
            risk = max(latest.close - invalidation, atr * 0.8, latest.close * 0.004)
            return TradeDecision(
                action=TradeAction.enter_call,
                confidence=0.86,
                reason=(
                    "Heuristic Advance long entry: GMMA golden cross confirmed, "
                    "short EMA band is above long EMA band, and OBV traffic light is bullish."
                ),
                decision_source="heuristic-advance",
                option_type="CE",
                invalidation_level=round(invalidation, 2),
                target_spot_price=round(latest.close + risk * 2.0, 2),
                first_target_price=round(latest.close + risk, 2),
                market_state="gmma_obv_bullish",
                setup_score=86.0,
                setup_type="advanced_gmma_obv_long",
                rule_ids_used=["ADV-GMMA-GC", "ADV-OBV-BULL", "ADV-STRUCTURE-SL"],
            )
        if state.gmma_dc and state.gmma_bear_regime and state.obv_bear:
            invalidation = self._defended_level(candles, bullish=False, atr=atr)
            risk = max(invalidation - latest.close, atr * 0.8, latest.close * 0.004)
            return TradeDecision(
                action=TradeAction.enter_put,
                confidence=0.86,
                reason=(
                    "Heuristic Advance short entry: GMMA death cross confirmed, "
                    "short EMA band is below long EMA band, and OBV traffic light is bearish."
                ),
                decision_source="heuristic-advance",
                option_type="PE",
                invalidation_level=round(invalidation, 2),
                target_spot_price=round(latest.close - risk * 2.0, 2),
                first_target_price=round(latest.close - risk, 2),
                market_state="gmma_obv_bearish",
                setup_score=86.0,
                setup_type="advanced_gmma_obv_short",
                rule_ids_used=["ADV-GMMA-DC", "ADV-OBV-BEAR", "ADV-STRUCTURE-SL"],
            )
        carry_forward = self._carry_forward_signal(candles)
        if carry_forward and self._carry_forward_confirmed(candles, state, carry_forward):
            bullish = carry_forward.side == "long"
            invalidation = self._defended_level(candles, bullish=bullish, atr=atr)
            risk = max(
                (latest.close - invalidation) if bullish else (invalidation - latest.close),
                atr * 0.8,
                latest.close * 0.004,
            )
            return TradeDecision(
                action=TradeAction.enter_call if bullish else TradeAction.enter_put,
                confidence=0.78,
                reason=(
                    f"Heuristic Advance carry-forward {'long' if bullish else 'short'} entry: "
                    f"GMMA/OBV crossed on the previous trading day near close at {carry_forward.timestamp_label}, "
                    "and today's opening action is confirming the same direction without waiting for a fresh crossover."
                ),
                decision_source="heuristic-advance",
                option_type="CE" if bullish else "PE",
                invalidation_level=round(invalidation, 2),
                target_spot_price=round(latest.close + risk * 2.0, 2) if bullish else round(latest.close - risk * 2.0, 2),
                first_target_price=round(latest.close + risk, 2) if bullish else round(latest.close - risk, 2),
                market_state="carry_forward_gmma_obv_bullish" if bullish else "carry_forward_gmma_obv_bearish",
                setup_score=78.0,
                setup_type="carry_forward_gmma_obv_long" if bullish else "carry_forward_gmma_obv_short",
                rule_ids_used=[
                    "ADV-CARRY-FORWARD-LATE-DAY-CROSS",
                    "ADV-OPEN-CONFIRMATION",
                    "ADV-STRUCTURE-SL",
                ],
            )
        return TradeDecision(
            action=TradeAction.no_trade,
            confidence=0.35,
            reason=(
                "Heuristic Advance mode found no entry because GMMA cross/regime "
                "and OBV traffic light are not aligned."
            ),
            decision_source="heuristic-advance",
            market_state="gmma_obv_mixed",
            setup_score=35.0,
            setup_type="advanced_no_entry",
        )

    def _carry_forward_signal(self, candles: list[Candle]) -> CarryForwardSignal | None:
        if len(candles) < self.minimum_candles + 1:
            return None
        latest_day = candles[-1].timestamp.date()
        previous_days = sorted({candle.timestamp.date() for candle in candles if candle.timestamp.date() < latest_day})
        if not previous_days:
            return None
        previous_day = previous_days[-1]
        latest_signal: CarryForwardSignal | None = None
        for index, candle in enumerate(candles):
            if candle.timestamp.date() != previous_day:
                continue
            candle_time = candle.timestamp.time()
            if candle_time < self.carry_forward_start or candle_time > self.carry_forward_end:
                continue
            prefix = candles[: index + 1]
            if len(prefix) < self.minimum_candles:
                continue
            state = self.state(prefix)
            if state.gmma_gc and state.gmma_bull_regime and state.obv_bull:
                latest_signal = CarryForwardSignal(
                    side="long",
                    timestamp_label=candle.timestamp.strftime("%H:%M"),
                    close=float(candle.close),
                )
            elif state.gmma_dc and state.gmma_bear_regime and state.obv_bear:
                latest_signal = CarryForwardSignal(
                    side="short",
                    timestamp_label=candle.timestamp.strftime("%H:%M"),
                    close=float(candle.close),
                )
        return latest_signal

    def _carry_forward_confirmed(
        self,
        candles: list[Candle],
        state: AdvancedIndicatorState,
        signal: CarryForwardSignal,
    ) -> bool:
        latest_day = candles[-1].timestamp.date()
        session = [candle for candle in candles if candle.timestamp.date() == latest_day]
        if not session:
            return False
        if len(session) > self.carry_forward_max_session_candles:
            return False
        latest = session[-1]
        first = session[0]
        session_high = max(candle.high for candle in session)
        session_low = min(candle.low for candle in session)
        if signal.side == "long":
            open_not_broken_badly = latest.close >= first.low
            opening_confirmation = latest.close >= first.close or latest.close >= first.open or latest.close >= signal.close
            trend_still_valid = state.obv_bull or state.gmma_bull_regime
            not_late_chase = latest.close <= session_low + max((session_high - session_low) * 0.85, latest.close * 0.02)
            return open_not_broken_badly and opening_confirmation and trend_still_valid and not_late_chase
        open_not_broken_badly = latest.close <= first.high
        opening_confirmation = latest.close <= first.close or latest.close <= first.open or latest.close <= signal.close
        trend_still_valid = state.obv_bear or state.gmma_bear_regime
        not_late_chase = latest.close >= session_high - max((session_high - session_low) * 0.85, latest.close * 0.02)
        return open_not_broken_badly and opening_confirmation and trend_still_valid and not_late_chase

    def state(self, candles: list[Candle]) -> AdvancedIndicatorState:
        closes = [float(candle.close) for candle in candles]
        short_emas = [self._ema_series(closes, length) for length in self.short_lengths]
        long_emas = [self._ema_series(closes, length) for length in self.long_lengths]
        short_latest = [series[-1] for series in short_emas]
        long_latest = [series[-1] for series in long_emas]
        short_prev = [series[-2] for series in short_emas]
        long_prev = [series[-2] for series in long_emas]
        prev_s6_cross = self._gmma_cross(short_prev[-1], long_prev)
        curr_s6_cross = self._gmma_cross(short_latest[-1], long_latest)
        cross_count = sum(self._gmma_cross(value, long_latest) for value in short_latest[:5])
        gmma_gc = cross_count == 5 and prev_s6_cross <= 0 < curr_s6_cross
        gmma_dc = cross_count == -5 and prev_s6_cross >= 0 > curr_s6_cross
        min_short = min(short_latest)
        max_short = max(short_latest)
        min_long = min(long_latest)
        max_long = max(long_latest)
        obv_values = self._obv_heikin_ashi(candles)
        obv_fast = self._ema_series(obv_values, self.obv_fast_len)
        obv_slow = self._ema_series(obv_values, self.obv_slow_len)
        return AdvancedIndicatorState(
            gmma_gc=gmma_gc,
            gmma_dc=gmma_dc,
            gmma_bull_regime=min_short > max_long,
            gmma_bear_regime=max_short < min_long,
            obv_bull=obv_values[-1] > obv_slow[-1] and obv_fast[-1] > obv_slow[-1],
            obv_bear=obv_values[-1] < obv_slow[-1] and obv_fast[-1] < obv_slow[-1],
            obv_value=obv_values[-1],
            obv_fast=obv_fast[-1],
            obv_slow=obv_slow[-1],
        )

    @staticmethod
    def _gmma_cross(short_value: float, long_values: list[float]) -> int:
        if all(short_value > value for value in long_values):
            return 1
        if all(short_value < value for value in long_values):
            return -1
        return 0

    @staticmethod
    def _ema_series(values: list[float], length: int) -> list[float]:
        if not values:
            return []
        alpha = 2.0 / (length + 1.0)
        ema_values = [float(values[0])]
        for value in values[1:]:
            ema_values.append((float(value) * alpha) + (ema_values[-1] * (1.0 - alpha)))
        return ema_values

    def _obv_heikin_ashi(self, candles: list[Candle]) -> list[float]:
        ha_open_values: list[float] = []
        ha_close_values: list[float] = []
        for index, candle in enumerate(candles):
            ha_close = (candle.open + candle.high + candle.low + candle.close) / 4.0
            if index == 0:
                ha_open = (candle.open + candle.close) / 2.0
            else:
                ha_open = (ha_open_values[-1] + ha_close_values[-1]) / 2.0
            ha_open_values.append(ha_open)
            ha_close_values.append(ha_close)
        obv_values: list[float] = []
        obv = 0.0
        for index, candle in enumerate(candles):
            open_val = ha_close_values[index - 1] if index > 0 else ha_open_values[index]
            close_val = ha_close_values[index]
            if close_val > open_val:
                vol = candle.volume
            elif close_val < open_val:
                vol = -candle.volume
            else:
                vol = 0.0
            obv += vol
            obv_values.append(obv)
        return obv_values

    @staticmethod
    def _atr(candles: list[Candle]) -> float:
        if not candles:
            return 1.0
        ranges = [max(candle.high - candle.low, 0.01) for candle in candles]
        return sum(ranges) / len(ranges)

    @staticmethod
    def _defended_level(candles: list[Candle], *, bullish: bool, atr: float) -> float:
        recent = candles[-6:] if len(candles) >= 6 else candles
        buffer = max(atr * 0.25, candles[-1].close * 0.001)
        if bullish:
            return min(candle.low for candle in recent) - buffer
        return max(candle.high for candle in recent) + buffer
