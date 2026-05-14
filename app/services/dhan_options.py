from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

try:  # pragma: no cover - depends on installed SDK version
    from dhanhq import DhanContext, dhanhq as DhanAPI
except Exception:  # pragma: no cover - optional runtime dependency path
    DhanContext = None
    DhanAPI = None


class DhanOptionQuoteError(RuntimeError):
    pass


@dataclass
class OptionQuote:
    security_id: str
    option_type: str
    strike: int
    last_price: float
    quote_time: datetime
    source: str
    bid_price: float | None = None
    ask_price: float | None = None
    volume: int | None = None
    oi: int | None = None


@dataclass
class OptionContract:
    security_id: str
    option_type: str
    strike: int
    expiry: date
    symbol: str
    quote: OptionQuote | None = None


class DhanOptionQuoteService:
    market_timezone = ZoneInfo("Asia/Kolkata")

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._expiry_cache: dict[tuple[int, str], tuple[datetime, list[date]]] = {}
        self._chain_cache: dict[tuple[int, str, date], tuple[datetime, dict]] = {}

    def resolve_option_contract(
        self,
        *,
        client_id: str,
        access_token: str,
        underlying_security_id: int,
        underlying_segment: str,
        strike: int,
        option_type: str,
        reference_time: datetime,
        underlying_label: str = "NIFTY",
    ) -> OptionContract:
        normalized_option_type = option_type.strip().upper()
        if normalized_option_type not in {"CE", "PE"}:
            raise DhanOptionQuoteError(f"Unsupported option type: {option_type}")

        expiry = self.get_nearest_expiry(
            client_id=client_id,
            access_token=access_token,
            underlying_security_id=underlying_security_id,
            underlying_segment=underlying_segment,
            reference_date=reference_time.date(),
        )
        chain = self.get_option_chain(
            client_id=client_id,
            access_token=access_token,
            underlying_security_id=underlying_security_id,
            underlying_segment=underlying_segment,
            expiry=expiry,
        )
        strike_payload = self._find_strike_payload(chain, strike)
        leg_key = "ce" if normalized_option_type == "CE" else "pe"
        leg_payload = strike_payload.get(leg_key) or {}
        security_id = leg_payload.get("security_id")
        if security_id in (None, ""):
            raise DhanOptionQuoteError(f"Dhan option chain did not return security_id for {strike} {normalized_option_type}.")

        quote = None
        quote_price = self._as_float(leg_payload.get("last_price"))
        if quote_price is not None:
            quote = OptionQuote(
                security_id=str(security_id),
                option_type=normalized_option_type,
                strike=strike,
                last_price=quote_price,
                quote_time=self._now_ist(),
                source="dhan-option-chain",
                bid_price=self._as_float(leg_payload.get("top_bid_price")),
                ask_price=self._as_float(leg_payload.get("top_ask_price")),
                volume=self._as_int(leg_payload.get("volume")),
                oi=self._as_int(leg_payload.get("oi")),
            )

        return OptionContract(
            security_id=str(security_id),
            option_type=normalized_option_type,
            strike=strike,
            expiry=expiry,
            symbol=f"{underlying_label} {expiry.strftime('%d%b%Y').upper()} {strike}{normalized_option_type}",
            quote=quote,
        )

    def fetch_quote(
        self,
        *,
        client_id: str,
        access_token: str,
        security_id: str,
        exchange_segment: str = "NSE_FNO",
        option_type: str = "",
        strike: int = 0,
    ) -> OptionQuote:
        client = self._client(client_id, access_token)
        response = client.quote_data({exchange_segment: [int(security_id)]})
        instrument_payload = (
            response.get("data", {})
            .get(exchange_segment, {})
            .get(str(security_id))
        )
        if not isinstance(instrument_payload, dict):
            raise DhanOptionQuoteError(f"Dhan quote API did not return quote data for option security {security_id}.")

        last_price = self._as_float(instrument_payload.get("last_price"))
        if last_price is None:
            raise DhanOptionQuoteError(f"Dhan quote API did not return last_price for option security {security_id}.")

        bid_price = None
        ask_price = None
        depth = instrument_payload.get("depth")
        if isinstance(depth, dict):
            buy_levels = depth.get("buy")
            sell_levels = depth.get("sell")
            if isinstance(buy_levels, list) and buy_levels:
                bid_price = self._as_float(buy_levels[0].get("price"))
            if isinstance(sell_levels, list) and sell_levels:
                ask_price = self._as_float(sell_levels[0].get("price"))

        return OptionQuote(
            security_id=str(security_id),
            option_type=option_type.strip().upper(),
            strike=int(strike or 0),
            last_price=last_price,
            quote_time=self._now_ist(),
            source="dhan-rest-quote",
            bid_price=bid_price,
            ask_price=ask_price,
            volume=self._as_int(instrument_payload.get("volume")),
            oi=self._as_int(instrument_payload.get("open_interest")),
        )

    def get_nearest_expiry(
        self,
        *,
        client_id: str,
        access_token: str,
        underlying_security_id: int,
        underlying_segment: str,
        reference_date: date,
    ) -> date:
        cache_key = (underlying_security_id, underlying_segment)
        with self._lock:
            cached = self._expiry_cache.get(cache_key)
            if cached and datetime.now(self.market_timezone) - cached[0] < timedelta(minutes=10):
                expiries = cached[1]
            else:
                client = self._client(client_id, access_token)
                response = client.expiry_list(underlying_security_id, underlying_segment)
                raw_expiries = response.get("data")
                if not isinstance(raw_expiries, list) or not raw_expiries:
                    raise DhanOptionQuoteError("Dhan expiry list API returned no active expiries.")
                expiries = [date.fromisoformat(value) for value in raw_expiries]
                self._expiry_cache[cache_key] = (datetime.now(self.market_timezone), expiries)

        for expiry in expiries:
            if expiry >= reference_date:
                return expiry
        return expiries[-1]

    def get_option_chain(
        self,
        *,
        client_id: str,
        access_token: str,
        underlying_security_id: int,
        underlying_segment: str,
        expiry: date,
    ) -> dict:
        cache_key = (underlying_security_id, underlying_segment, expiry)
        with self._lock:
            cached = self._chain_cache.get(cache_key)
            if cached and datetime.now(self.market_timezone) - cached[0] < timedelta(seconds=10):
                return cached[1]

        client = self._client(client_id, access_token)
        response = client.option_chain(
            under_security_id=underlying_security_id,
            under_exchange_segment=underlying_segment,
            expiry=expiry.isoformat(),
        )
        data = response.get("data", {})
        chain = data.get("oc")
        if not isinstance(chain, dict) or not chain:
            raise DhanOptionQuoteError("Dhan option chain API returned no strike data.")

        with self._lock:
            self._chain_cache[cache_key] = (datetime.now(self.market_timezone), chain)
        return chain

    def _client(self, client_id: str, access_token: str):
        if DhanContext is None or DhanAPI is None:
            raise DhanOptionQuoteError("dhanhq package is not available in this environment.")
        context = DhanContext(client_id, access_token)
        return DhanAPI(context)

    def _find_strike_payload(self, chain: dict, strike: int) -> dict:
        exact_key = f"{float(strike):.6f}"
        if exact_key in chain and isinstance(chain[exact_key], dict):
            return chain[exact_key]

        nearest_key = None
        nearest_distance = None
        for key, payload in chain.items():
            if not isinstance(payload, dict):
                continue
            try:
                key_strike = int(round(float(key)))
            except (TypeError, ValueError):
                continue
            distance = abs(key_strike - strike)
            if nearest_distance is None or distance < nearest_distance:
                nearest_distance = distance
                nearest_key = key
        if nearest_key is None:
            raise DhanOptionQuoteError(f"No option chain strike found near {strike}.")
        return chain[nearest_key]

    def _now_ist(self) -> datetime:
        return datetime.now(self.market_timezone)

    def _as_float(self, value) -> float | None:
        if value in (None, ""):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _as_int(self, value) -> int | None:
        if value in (None, ""):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
