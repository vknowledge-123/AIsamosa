from __future__ import annotations

import csv
import io
import math
from collections import defaultdict
from datetime import date, datetime, time, timedelta

from app.schemas import Candle, PreviousDayLevels


def parse_candle_csv(content: bytes) -> list[Candle]:
    text = _decode_text_content(content)
    reader = csv.DictReader(io.StringIO(text))
    candles: list[Candle] = []
    for row in reader:
        normalized = {_normalize_header(key): value for key, value in row.items()}
        candles.append(
            Candle(
                timestamp=datetime.fromisoformat(normalized["timestamp"]),
                open=float(normalized["open"]),
                high=float(normalized["high"]),
                low=float(normalized["low"]),
                close=float(normalized["close"]),
                volume=float(normalized.get("volume") or 0.0),
            )
        )
    candles.sort(key=lambda candle: candle.timestamp)
    return candles


def _decode_text_content(content: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16", "utf-16-le", "utf-16-be", "latin-1"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("Unable to decode the uploaded candle file. Save it as UTF-8 or UTF-16 CSV.")


def _normalize_header(value: str | None) -> str:
    return (value or "").replace("\ufeff", "").strip().lower()


def generate_sample_candles() -> list[Candle]:
    candles: list[Candle] = []
    base_date = date(2026, 4, 23)
    start_price = 24420.0
    interval = timedelta(minutes=1)

    for day_offset in range(2):
        session_date = base_date + timedelta(days=day_offset)
        session_start = datetime.combine(session_date, time(9, 15))
        for index in range(90):
            ts = session_start + index * interval
            drift = 38 * math.sin((index + 5 * day_offset) / 8)
            trend = day_offset * 55 + index * 0.9
            pulse = 22 if index in {15, 16, 17, 52, 53, 54} else 0
            reversal = -28 if index in {30, 31, 61, 62} else 0
            close = start_price + trend + drift + pulse + reversal
            open_price = close - math.sin(index / 4) * 6
            high = max(open_price, close) + 6 + (index % 3)
            low = min(open_price, close) - 6 - (index % 4)
            candles.append(
                Candle(
                    timestamp=ts,
                    open=round(open_price, 2),
                    high=round(high, 2),
                    low=round(low, 2),
                    close=round(close, 2),
                    volume=1500 + (index * 17) + (day_offset * 100),
                )
            )
        start_price += 70

    return candles


def calculate_previous_day_levels(candles: list[Candle], current_index: int) -> PreviousDayLevels:
    if not candles or current_index <= 0:
        return PreviousDayLevels()

    current_day = candles[current_index].timestamp.date()
    daily_buckets = _daily_buckets(candles[: current_index + 1])

    available_days = sorted(daily_buckets.keys())
    try:
        current_day_position = available_days.index(current_day)
    except ValueError:
        return PreviousDayLevels()

    if current_day_position == 0:
        return PreviousDayLevels()

    previous_day_candles = daily_buckets[available_days[current_day_position - 1]]
    return PreviousDayLevels(
        high=max(candle.high for candle in previous_day_candles),
        low=min(candle.low for candle in previous_day_candles),
        close=previous_day_candles[-1].close,
    )


def calculate_previous_day_levels_for_timestamp(
    candles: list[Candle],
    reference_timestamp: datetime,
) -> PreviousDayLevels:
    if not candles:
        return PreviousDayLevels()

    reference_day = reference_timestamp.date()
    daily_buckets = _daily_buckets(candles)

    previous_days = sorted(day for day in daily_buckets.keys() if day < reference_day)
    if not previous_days:
        return PreviousDayLevels()

    previous_day_candles = daily_buckets[previous_days[-1]]
    return PreviousDayLevels(
        high=max(candle.high for candle in previous_day_candles),
        low=min(candle.low for candle in previous_day_candles),
        close=previous_day_candles[-1].close,
    )


def get_session_candles_up_to_index(candles: list[Candle], current_index: int) -> list[Candle]:
    if not candles or current_index < 0 or current_index >= len(candles):
        return []

    session_day = candles[current_index].timestamp.date()
    return [candle for candle in candles[: current_index + 1] if candle.timestamp.date() == session_day]


def get_previous_day_candles(candles: list[Candle], current_index: int) -> list[Candle]:
    if not candles or current_index <= 0 or current_index >= len(candles):
        return []

    current_day = candles[current_index].timestamp.date()
    daily_buckets = _daily_buckets(candles[: current_index + 1])
    previous_days = sorted(day for day in daily_buckets.keys() if day < current_day)
    if not previous_days:
        return []
    return list(daily_buckets[previous_days[-1]])


def _daily_buckets(candles: list[Candle]) -> dict[date, list[Candle]]:
    buckets: dict[date, list[Candle]] = defaultdict(list)
    for candle in candles:
        buckets[candle.timestamp.date()].append(candle)
    return buckets
