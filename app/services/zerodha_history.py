from __future__ import annotations

from datetime import date, datetime, time, timedelta
import threading
import time as time_module
from typing import Any
from zoneinfo import ZoneInfo

from app.schemas import Candle
from app.services.dhan_history import DhanChartEmptyDataError, DhanChartError, DhanSessionBundle
from app.services.zerodha_execution import ZerodhaExecutionError, ZerodhaExecutionService


class ZerodhaChartService:
    market_timezone = ZoneInfo("Asia/Kolkata")
    session_open = time(9, 15)
    session_close = time(15, 30)
    min_request_gap_seconds = 0.35

    def __init__(self, execution_service: ZerodhaExecutionService) -> None:
        self.execution_service = execution_service
        self._request_gap_lock = threading.Lock()
        self._last_request_started_at = 0.0

    def fetch_market_context(
        self,
        api_key: str,
        access_token: str,
        *,
        symbol: str,
        exchange_segment: str,
        tradingsymbol: str | None = None,
        prefer_last_closed_session_before_open: bool = False,
        market_now: datetime | None = None,
        **_: Any,
    ) -> DhanSessionBundle:
        market_now = market_now or datetime.now(self.market_timezone)
        session_day = self._effective_session_day(
            market_now,
            prefer_last_closed_session_before_open=prefer_last_closed_session_before_open,
        )
        use_closed_session_replay = (
            prefer_last_closed_session_before_open
            and market_now.timetz().replace(tzinfo=None) < self.session_open
        )
        if use_closed_session_replay:
            intraday_candles, intraday_source, session_day = self.fetch_latest_available_session_day_candles(
                api_key,
                access_token,
                symbol=symbol,
                exchange_segment=exchange_segment,
                tradingsymbol=tradingsymbol,
                session_day=session_day,
            )
            live_open_candle = None
        else:
            intraday_candles, live_open_candle = self.fetch_intraday_candles(
                api_key,
                access_token,
                symbol=symbol,
                exchange_segment=exchange_segment,
                tradingsymbol=tradingsymbol,
                session_day=session_day,
                market_now=market_now,
            )
            intraday_source = "zerodha-historical"

        previous_candidate = self._previous_trading_day(session_day)
        previous_candles, previous_source, previous_context_day = self.fetch_latest_available_session_day_candles(
            api_key,
            access_token,
            symbol=symbol,
            exchange_segment=exchange_segment,
            tradingsymbol=tradingsymbol,
            session_day=previous_candidate,
        )
        return DhanSessionBundle(
            previous_day_candles=previous_candles,
            intraday_candles=intraday_candles,
            live_open_candle=live_open_candle,
            previous_day_source=previous_source,
            replay_session_day=session_day,
            intraday_source=intraday_source,
            previous_context_day=previous_context_day,
        )

    def fetch_market_context_for_days(
        self,
        api_key: str,
        access_token: str,
        *,
        session_day: date,
        previous_context_day: date,
        symbol: str,
        exchange_segment: str,
        tradingsymbol: str | None = None,
        **_: Any,
    ) -> DhanSessionBundle:
        if previous_context_day >= session_day:
            raise ValueError("Previous-day context must be earlier than the replay session day.")
        previous_candles, previous_source = self.fetch_session_day_candles(
            api_key,
            access_token,
            symbol=symbol,
            exchange_segment=exchange_segment,
            tradingsymbol=tradingsymbol,
            session_day=previous_context_day,
        )
        replay_candles, replay_source = self.fetch_session_day_candles(
            api_key,
            access_token,
            symbol=symbol,
            exchange_segment=exchange_segment,
            tradingsymbol=tradingsymbol,
            session_day=session_day,
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

    def fetch_latest_available_session_day_candles(
        self,
        api_key: str,
        access_token: str,
        *,
        symbol: str,
        exchange_segment: str,
        session_day: date,
        tradingsymbol: str | None = None,
        max_lookback_sessions: int = 7,
        **_: Any,
    ) -> tuple[list[Candle], str, date]:
        candidate_day = session_day
        empty_errors: list[str] = []
        for _ in range(max_lookback_sessions):
            try:
                candles, source = self.fetch_session_day_candles(
                    api_key,
                    access_token,
                    symbol=symbol,
                    exchange_segment=exchange_segment,
                    tradingsymbol=tradingsymbol,
                    session_day=candidate_day,
                )
                return candles, source, candidate_day
            except DhanChartEmptyDataError as exc:
                empty_errors.append(str(exc))
                candidate_day = self._previous_trading_day(candidate_day)
        detail = empty_errors[-1] if empty_errors else f"No candles found on or before {session_day}."
        raise DhanChartEmptyDataError(
            f"No available Zerodha trading-day candles were found for {symbol} "
            f"within {max_lookback_sessions} trading sessions on or before {session_day}. {detail}"
        )

    def fetch_session_day_candles(
        self,
        api_key: str,
        access_token: str,
        *,
        symbol: str,
        exchange_segment: str,
        session_day: date,
        tradingsymbol: str | None = None,
        interval: str = "minute",
        **_: Any,
    ) -> tuple[list[Candle], str]:
        session_start = datetime.combine(session_day, self.session_open)
        session_end = datetime.combine(session_day, self.session_close)
        candles = self._request_historical_candles(
            api_key=api_key,
            access_token=access_token,
            symbol=symbol,
            exchange_segment=exchange_segment,
            tradingsymbol=tradingsymbol,
            from_datetime=session_start,
            to_datetime=session_end,
            interval=interval,
        )
        candles = [candle for candle in candles if candle.timestamp.date() == session_day]
        if not candles:
            raise DhanChartEmptyDataError(
                f"Zerodha historical API returned no candles for {symbol} between "
                f"{session_start:%Y-%m-%d %H:%M:%S} and {session_end:%Y-%m-%d %H:%M:%S}."
            )
        return candles, "zerodha-historical"

    def fetch_intraday_candles(
        self,
        api_key: str,
        access_token: str,
        *,
        symbol: str,
        exchange_segment: str,
        session_day: date,
        market_now: datetime,
        tradingsymbol: str | None = None,
    ) -> tuple[list[Candle], Candle | None]:
        session_start = datetime.combine(session_day, self.session_open, tzinfo=self.market_timezone)
        session_end = datetime.combine(session_day, self.session_close, tzinfo=self.market_timezone)
        if market_now <= session_start:
            return [], None
        request_end = min(market_now, session_end).replace(tzinfo=None)
        candles = self._request_historical_candles(
            api_key=api_key,
            access_token=access_token,
            symbol=symbol,
            exchange_segment=exchange_segment,
            tradingsymbol=tradingsymbol,
            from_datetime=session_start.replace(tzinfo=None),
            to_datetime=request_end,
            interval="minute",
        )
        if not candles:
            return [], None
        current_bucket = market_now.replace(second=0, microsecond=0, tzinfo=None)
        if market_now < session_end and candles[-1].timestamp == current_bucket:
            return candles[:-1], candles[-1]
        return candles, None

    def _request_historical_candles(
        self,
        *,
        api_key: str,
        access_token: str,
        symbol: str,
        exchange_segment: str,
        from_datetime: datetime,
        to_datetime: datetime,
        tradingsymbol: str | None,
        interval: str,
    ) -> list[Candle]:
        try:
            instrument = self.execution_service.resolve_feed_instrument(
                api_key=api_key,
                access_token=access_token,
                symbol=symbol,
                exchange_segment=exchange_segment,
                tradingsymbol=tradingsymbol or symbol,
            )
        except ZerodhaExecutionError as exc:
            raise DhanChartError(str(exc)) from exc
        if instrument.instrument_token is None:
            raise DhanChartError(f"Could not resolve Zerodha instrument token for {symbol}.")
        kite = self.execution_service._client(api_key=api_key, access_token=access_token)
        self._throttle_request_start()
        try:
            rows = kite.historical_data(
                instrument.instrument_token,
                from_datetime,
                to_datetime,
                interval,
                continuous=False,
                oi=False,
            )
        except Exception as exc:
            raise DhanChartError(f"Zerodha historical API error: {exc}") from exc
        candles = [self._row_to_candle(row) for row in rows or [] if isinstance(row, dict)]
        candles = [candle for candle in candles if candle is not None]
        candles.sort(key=lambda candle: candle.timestamp)
        return candles

    def _row_to_candle(self, row: dict[str, Any]) -> Candle | None:
        raw_date = row.get("date")
        timestamp = self._parse_timestamp(raw_date)
        if timestamp is None:
            return None
        return Candle(
            timestamp=timestamp,
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row.get("volume") or 0.0),
        )

    def _parse_timestamp(self, raw_value: Any) -> datetime | None:
        if raw_value in (None, ""):
            return None
        if isinstance(raw_value, datetime):
            if raw_value.tzinfo is not None:
                return raw_value.astimezone(self.market_timezone).replace(tzinfo=None)
            return raw_value
        try:
            parsed = datetime.fromisoformat(str(raw_value).replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is not None:
            return parsed.astimezone(self.market_timezone).replace(tzinfo=None)
        return parsed

    def _throttle_request_start(self) -> None:
        with self._request_gap_lock:
            now = time_module.monotonic()
            wait_seconds = self.min_request_gap_seconds - (now - self._last_request_started_at)
            if wait_seconds > 0:
                time_module.sleep(wait_seconds)
            self._last_request_started_at = time_module.monotonic()

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
