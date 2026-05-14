from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from app.schemas import Candle


class DhanChartError(RuntimeError):
    pass


@dataclass
class DhanSessionBundle:
    previous_day_candles: list[Candle]
    intraday_candles: list[Candle]
    live_open_candle: Candle | None
    previous_day_source: str


class DhanChartService:
    intraday_url = "https://api.dhan.co/v2/charts/intraday"
    historical_url = "https://api.dhan.co/v2/charts/historical"
    market_timezone = ZoneInfo("Asia/Kolkata")
    session_open = time(9, 15)
    session_close = time(15, 30)

    def fetch_market_context(
        self,
        client_id: str,
        access_token: str,
        security_id: str = "13",
        exchange_segment: str = "IDX_I",
        instrument_type: str = "INDEX",
    ) -> DhanSessionBundle:
        market_now = datetime.now(self.market_timezone)
        session_day = market_now.date()
        previous_day = self._previous_trading_day(session_day)
        previous_candles, previous_source = self.fetch_previous_day_candles(
            client_id,
            access_token,
            security_id,
            previous_day,
            exchange_segment,
            instrument_type,
        )
        intraday_candles, live_open_candle = self.fetch_intraday_candles(
            client_id,
            access_token,
            security_id,
            session_day,
            market_now,
            exchange_segment,
            instrument_type,
        )
        return DhanSessionBundle(
            previous_day_candles=previous_candles,
            intraday_candles=intraday_candles,
            live_open_candle=live_open_candle,
            previous_day_source=previous_source,
        )

    def fetch_previous_day_candles(
        self,
        client_id: str,
        access_token: str,
        security_id: str,
        session_day: date,
        exchange_segment: str,
        instrument_type: str,
    ) -> tuple[list[Candle], str]:
        payload = {
            "securityId": str(security_id),
            "exchangeSegment": exchange_segment,
            "instrument": instrument_type,
            "expiryCode": 0,
            "interval": 1,
            "oi": False,
            "fromDate": session_day.strftime("%Y-%m-%d"),
            "toDate": session_day.strftime("%Y-%m-%d"),
        }
        try:
            candles = self._request_candles(
                url=self.historical_url,
                payload=payload,
                client_id=client_id,
                access_token=access_token,
            )
            candles = [candle for candle in candles if candle.timestamp.date() == session_day]
            if candles and self._is_intraday_series(candles):
                return candles, "historical"
        except DhanChartError:
            pass

        session_start = datetime.combine(session_day, self.session_open)
        session_end = datetime.combine(session_day, self.session_close)
        candles = self._request_intraday_window(
            client_id=client_id,
            access_token=access_token,
            security_id=security_id,
            window_start=session_start,
            window_end=session_end,
            exchange_segment=exchange_segment,
            instrument_type=instrument_type,
        )
        return candles, "intraday-fallback"

    def fetch_intraday_candles(
        self,
        client_id: str,
        access_token: str,
        security_id: str,
        session_day: date,
        market_now: datetime,
        exchange_segment: str,
        instrument_type: str,
    ) -> tuple[list[Candle], Candle | None]:
        session_start = datetime.combine(session_day, self.session_open, tzinfo=self.market_timezone)
        session_end = datetime.combine(session_day, self.session_close, tzinfo=self.market_timezone)
        if market_now <= session_start:
            return [], None

        request_end = min(market_now, session_end)
        candles = self._request_intraday_window(
            client_id=client_id,
            access_token=access_token,
            security_id=security_id,
            window_start=session_start.replace(tzinfo=None),
            window_end=request_end.replace(tzinfo=None),
            exchange_segment=exchange_segment,
            instrument_type=instrument_type,
        )
        if not candles:
            return [], None

        current_bucket = market_now.replace(second=0, microsecond=0, tzinfo=None)
        if market_now < session_end and candles[-1].timestamp == current_bucket:
            return candles[:-1], candles[-1]
        return candles, None

    def _request_intraday_window(
        self,
        *,
        client_id: str,
        access_token: str,
        security_id: str,
        window_start: datetime,
        window_end: datetime,
        exchange_segment: str,
        instrument_type: str,
    ) -> list[Candle]:
        payload = {
            "securityId": str(security_id),
            "exchangeSegment": exchange_segment,
            "instrument": instrument_type,
            "interval": 1,
            "oi": False,
            "fromDate": window_start.strftime("%Y-%m-%d %H:%M:%S"),
            "toDate": window_end.strftime("%Y-%m-%d %H:%M:%S"),
        }
        return self._request_candles(
            url=self.intraday_url,
            payload=payload,
            client_id=client_id,
            access_token=access_token,
        )

    def _request_candles(
        self,
        *,
        url: str,
        payload: dict[str, Any],
        client_id: str,
        access_token: str,
    ) -> list[Candle]:
        headers = {
            "client-id": client_id,
            "access-token": access_token,
            "Content-Type": "application/json",
        }
        try:
            with httpx.Client(timeout=20.0) as client:
                response = client.post(url, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise DhanChartError(f"Unable to reach Dhan chart API: {exc}") from exc

        try:
            body = response.json()
        except ValueError as exc:
            raise DhanChartError(f"Dhan chart API returned non-JSON response ({response.status_code}).") from exc

        if response.status_code != 200:
            remarks = body.get("remarks") if isinstance(body, dict) else None
            raise DhanChartError(remarks or f"Dhan chart API error {response.status_code}.")

        candles = self._parse_candles(body)
        if not candles:
            raise DhanChartError("Dhan chart API returned no candles for the requested range.")
        return candles

    def _parse_candles(self, payload: Any) -> list[Candle]:
        data = payload.get("data", payload) if isinstance(payload, dict) else payload
        rows = self._extract_rows(data)
        candles: list[Candle] = []
        for row in rows:
            timestamp = self._parse_timestamp(row.get("timestamp"))
            if timestamp is None:
                continue
            candles.append(
                Candle(
                    timestamp=timestamp,
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row.get("volume") or 0.0),
                )
            )
        candles.sort(key=lambda candle: candle.timestamp)
        return candles

    def _extract_rows(self, data: Any) -> list[dict[str, Any]]:
        if isinstance(data, list):
            return [row for row in data if isinstance(row, dict)]

        if not isinstance(data, dict):
            return []

        timestamps = data.get("timestamp")
        opens = data.get("open")
        highs = data.get("high")
        lows = data.get("low")
        closes = data.get("close")
        volumes = data.get("volume") or []

        if all(isinstance(series, list) for series in (timestamps, opens, highs, lows, closes)):
            size = min(len(timestamps), len(opens), len(highs), len(lows), len(closes))
            rows: list[dict[str, Any]] = []
            for index in range(size):
                rows.append(
                    {
                        "timestamp": timestamps[index],
                        "open": opens[index],
                        "high": highs[index],
                        "low": lows[index],
                        "close": closes[index],
                        "volume": volumes[index] if isinstance(volumes, list) and index < len(volumes) else 0.0,
                    }
                )
            return rows

        if {"timestamp", "open", "high", "low", "close"}.issubset(data.keys()):
            return [data]

        return []

    def _parse_timestamp(self, raw_value: Any) -> datetime | None:
        if raw_value in (None, ""):
            return None
        if isinstance(raw_value, (int, float)):
            return self._from_epoch(raw_value)
        if isinstance(raw_value, str):
            raw_value = raw_value.strip()
            if not raw_value:
                return None
            try:
                return self._from_epoch(float(raw_value))
            except ValueError:
                pass
            try:
                parsed = datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
            except ValueError:
                return None
            if parsed.tzinfo is None:
                return parsed
            return parsed.astimezone(self.market_timezone).replace(tzinfo=None)
        return None

    def _from_epoch(self, raw_value: int | float) -> datetime:
        timestamp = float(raw_value)
        if timestamp > 1_000_000_000_000:
            timestamp /= 1000.0
        return datetime.fromtimestamp(timestamp, tz=self.market_timezone).replace(tzinfo=None)

    def _is_intraday_series(self, candles: list[Candle]) -> bool:
        if len(candles) < 2:
            return False
        first_gap = candles[1].timestamp - candles[0].timestamp
        return first_gap <= timedelta(minutes=1, seconds=5)

    def _previous_trading_day(self, session_day: date) -> date:
        previous = session_day - timedelta(days=1)
        while previous.weekday() >= 5:
            previous -= timedelta(days=1)
        return previous
