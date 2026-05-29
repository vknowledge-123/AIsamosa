from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
import threading
import time as time_module
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from app.schemas import Candle


class DhanChartError(RuntimeError):
    pass


class DhanChartRateLimitError(DhanChartError):
    pass


class DhanChartEmptyDataError(DhanChartError):
    pass


@dataclass
class DhanSessionBundle:
    previous_day_candles: list[Candle]
    intraday_candles: list[Candle]
    live_open_candle: Candle | None
    previous_day_source: str
    replay_session_day: date | None = None
    intraday_source: str = "intraday"
    previous_context_day: date | None = None


class DhanChartService:
    intraday_url = "https://api.dhan.co/v2/charts/intraday"
    historical_url = "https://api.dhan.co/v2/charts/historical"
    market_timezone = ZoneInfo("Asia/Kolkata")
    session_open = time(9, 15)
    session_close = time(15, 30)
    intraday_request_lead = timedelta(minutes=1)
    min_request_gap_seconds = 0.35
    max_rate_limit_retries = 2

    def __init__(self) -> None:
        self._request_gap_lock = threading.Lock()
        self._last_request_started_at = 0.0

    def fetch_market_context(
        self,
        client_id: str,
        access_token: str,
        security_id: str = "13",
        exchange_segment: str = "IDX_I",
        instrument_type: str = "INDEX",
        prefer_last_closed_session_before_open: bool = False,
        market_now: datetime | None = None,
    ) -> DhanSessionBundle:
        market_now = market_now or datetime.now(self.market_timezone)
        use_closed_session_replay = (
            prefer_last_closed_session_before_open
            and market_now.timetz().replace(tzinfo=None) < self.session_open
        )
        session_day = self._effective_session_day(
            market_now,
            prefer_last_closed_session_before_open=prefer_last_closed_session_before_open,
        )
        intraday_source = "intraday"
        if use_closed_session_replay:
            intraday_candles, intraday_source, session_day = self.fetch_latest_available_session_day_candles(
                client_id,
                access_token,
                security_id,
                session_day,
                exchange_segment,
                instrument_type,
            )
            live_open_candle = None
        else:
            intraday_candles, live_open_candle = self.fetch_intraday_candles(
                client_id,
                access_token,
                security_id,
                session_day,
                market_now,
                exchange_segment,
                instrument_type,
            )
        previous_day = self._previous_trading_day(session_day)
        previous_candles, previous_source, previous_day = self.fetch_latest_available_session_day_candles(
            client_id,
            access_token,
            security_id,
            previous_day,
            exchange_segment,
            instrument_type,
        )
        return DhanSessionBundle(
            previous_day_candles=previous_candles,
            intraday_candles=intraday_candles,
            live_open_candle=live_open_candle,
            previous_day_source=previous_source,
            replay_session_day=session_day,
            intraday_source=intraday_source,
            previous_context_day=previous_day,
        )

    def fetch_latest_available_session_day_candles(
        self,
        client_id: str,
        access_token: str,
        security_id: str,
        session_day: date,
        exchange_segment: str,
        instrument_type: str,
        *,
        max_lookback_sessions: int = 7,
    ) -> tuple[list[Candle], str, date]:
        candidate_day = session_day
        empty_errors: list[str] = []
        for _ in range(max_lookback_sessions):
            try:
                candles, source = self.fetch_session_day_candles(
                    client_id,
                    access_token,
                    security_id,
                    candidate_day,
                    exchange_segment,
                    instrument_type,
                )
                return candles, source, candidate_day
            except DhanChartEmptyDataError as exc:
                empty_errors.append(str(exc))
                candidate_day = self._previous_trading_day(candidate_day)
        detail = empty_errors[-1] if empty_errors else f"No candles found on or before {session_day}."
        raise DhanChartEmptyDataError(
            f"No available trading-day candles were found for security {security_id} "
            f"within {max_lookback_sessions} trading sessions on or before {session_day}. {detail}"
        )

    def fetch_market_context_for_days(
        self,
        client_id: str,
        access_token: str,
        *,
        session_day: date,
        previous_context_day: date,
        security_id: str = "13",
        exchange_segment: str = "IDX_I",
        instrument_type: str = "INDEX",
    ) -> DhanSessionBundle:
        if previous_context_day >= session_day:
            raise ValueError("Previous-day context must be earlier than the replay session day.")
        previous_candles, previous_source = self.fetch_session_day_candles(
            client_id,
            access_token,
            security_id,
            previous_context_day,
            exchange_segment,
            instrument_type,
        )
        replay_candles, replay_source = self.fetch_session_day_candles(
            client_id,
            access_token,
            security_id,
            session_day,
            exchange_segment,
            instrument_type,
        )
        return DhanSessionBundle(
            previous_day_candles=previous_candles,
            intraday_candles=replay_candles,
            live_open_candle=None,
            previous_day_source=previous_source,
            replay_session_day=session_day,
            intraday_source=replay_source,
            previous_context_day=previous_context_day,
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
        return self.fetch_session_day_candles(
            client_id,
            access_token,
            security_id,
            session_day,
            exchange_segment,
            instrument_type,
        )

    def fetch_session_day_candles(
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
        except DhanChartRateLimitError:
            raise
        except DhanChartError:
            pass

        session_start = self._intraday_request_start(session_day)
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
        request_start = self._intraday_request_start(session_day).replace(tzinfo=self.market_timezone)
        session_end = datetime.combine(session_day, self.session_close, tzinfo=self.market_timezone)
        if market_now <= session_start:
            return [], None

        request_end = min(market_now, session_end)
        candles = self._request_intraday_window(
            client_id=client_id,
            access_token=access_token,
            security_id=security_id,
            window_start=request_start.replace(tzinfo=None),
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

    def _intraday_request_start(self, session_day: date) -> datetime:
        session_start = datetime.combine(session_day, self.session_open)
        return session_start - self.intraday_request_lead

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
        attempts = self.max_rate_limit_retries + 1
        headers = {
            "client-id": client_id,
            "access-token": access_token,
            "Content-Type": "application/json",
        }
        for attempt in range(attempts):
            self._throttle_request_start()
            try:
                with httpx.Client(timeout=20.0) as client:
                    response = client.post(url, json=payload, headers=headers)
            except httpx.HTTPError as exc:
                raise DhanChartError(f"Unable to reach Dhan chart API: {exc}") from exc

            body = None
            try:
                body = response.json()
            except ValueError as exc:
                if response.status_code == 200:
                    raise DhanChartError(f"Dhan chart API returned non-JSON response ({response.status_code}).") from exc

            if response.status_code == 429:
                wait_seconds = self._retry_after_seconds(response.headers.get("Retry-After"))
                if wait_seconds is None:
                    wait_seconds = float(attempt + 1)
                if attempt < attempts - 1:
                    time_module.sleep(wait_seconds)
                    continue
                retry_text = f" Retry after about {wait_seconds:g}s." if wait_seconds > 0 else ""
                remarks = body.get("remarks") if isinstance(body, dict) else None
                raise DhanChartRateLimitError(
                    remarks or f"Dhan chart API rate limit hit (429).{retry_text}"
                )

            if response.status_code != 200:
                remarks = body.get("remarks") if isinstance(body, dict) else None
                raise DhanChartError(remarks or f"Dhan chart API error {response.status_code}.")

            candles = self._parse_candles(body)
            if not candles:
                from_date = payload.get("fromDate", "?")
                to_date = payload.get("toDate", "?")
                security_id = payload.get("securityId", "?")
                endpoint = "intraday" if url == self.intraday_url else "historical"
                raise DhanChartEmptyDataError(
                    f"Dhan {endpoint} chart API returned no candles for security {security_id} "
                    f"between {from_date} and {to_date}."
                )
            return candles

        raise DhanChartError("Dhan chart API request failed after retry attempts.")

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

    def _throttle_request_start(self) -> None:
        with self._request_gap_lock:
            now = time_module.monotonic()
            wait_seconds = self.min_request_gap_seconds - (now - self._last_request_started_at)
            if wait_seconds > 0:
                time_module.sleep(wait_seconds)
            self._last_request_started_at = time_module.monotonic()

    def _retry_after_seconds(self, raw_value: str | None) -> float | None:
        if raw_value in (None, ""):
            return None
        try:
            return max(float(raw_value), 0.0)
        except (TypeError, ValueError):
            return None

    def _previous_trading_day(self, session_day: date) -> date:
        previous = session_day - timedelta(days=1)
        while previous.weekday() >= 5:
            previous -= timedelta(days=1)
        return previous

    def _effective_session_day(
        self,
        market_now: datetime,
        *,
        prefer_last_closed_session_before_open: bool,
    ) -> date:
        session_day = market_now.date()
        if prefer_last_closed_session_before_open and market_now.timetz().replace(tzinfo=None) < self.session_open:
            return self._previous_trading_day(session_day)
        return session_day
