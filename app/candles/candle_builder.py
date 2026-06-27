from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.schemas import Candle


@dataclass(frozen=True)
class CandleUpdate:
    candles: list[Candle]
    current_candle: Candle
    current_index: int
    evaluation_index: int | None


def update_live_minute_candle(
    *,
    candles: list[Candle],
    current_candle: Candle | None,
    current_index: int,
    tick_time: datetime,
    ltp: float,
    volume: float,
) -> CandleUpdate:
    bucket = tick_time.replace(second=0, microsecond=0)
    if current_candle is None:
        return CandleUpdate(
            candles=candles,
            current_candle=Candle(timestamp=bucket, open=ltp, high=ltp, low=ltp, close=ltp, volume=volume),
            current_index=current_index,
            evaluation_index=None,
        )

    if bucket == current_candle.timestamp:
        current_candle.high = max(current_candle.high, ltp)
        current_candle.low = min(current_candle.low, ltp)
        current_candle.close = ltp
        current_candle.volume += max(volume, 0.0)
        return CandleUpdate(
            candles=candles,
            current_candle=current_candle,
            current_index=current_index,
            evaluation_index=None,
        )

    completed_candle = current_candle
    if candles and candles[-1].timestamp == completed_candle.timestamp:
        candles[-1] = completed_candle
    else:
        candles.append(completed_candle)
    next_index = len(candles) - 1
    return CandleUpdate(
        candles=candles,
        current_candle=Candle(timestamp=bucket, open=ltp, high=ltp, low=ltp, close=ltp, volume=volume),
        current_index=next_index,
        evaluation_index=next_index,
    )

