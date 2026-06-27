from __future__ import annotations

from dataclasses import dataclass, field

from app.schemas import Candle


@dataclass
class CandleStore:
    candles: list[Candle] = field(default_factory=list)
    current_index: int = -1
    live_current_candle: Candle | None = None

