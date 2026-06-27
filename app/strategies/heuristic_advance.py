from __future__ import annotations

from app.schemas import Candle, TradeDecision
from app.services.advanced_indicator_engine import AdvancedIndicatorEngine


def decide_advanced(engine: AdvancedIndicatorEngine, candles: list[Candle]) -> TradeDecision:
    return engine.decide(candles)
