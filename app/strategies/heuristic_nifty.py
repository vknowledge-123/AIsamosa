from __future__ import annotations

from app.schemas import StrategyContext, TradeDecision
from app.services.heuristic_engine import HeuristicDecisionEngine


def decide_nifty(
    engine: HeuristicDecisionEngine,
    context: StrategyContext,
    *,
    current_trade_price: float | None = None,
) -> TradeDecision:
    return engine.decide(context, current_trade_price=current_trade_price)
