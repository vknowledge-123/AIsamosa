from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import threading

from app.services.dhan_execution import BrokerOrderResult


class ZerodhaExecutionError(RuntimeError):
    pass


@dataclass(frozen=True)
class ZerodhaInstrument:
    tradingsymbol: str
    exchange: str
    instrument_token: int | None
    name: str
    expiry: date | None
    strike: float
    lot_size: int
    instrument_type: str


class ZerodhaExecutionService:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._instrument_cache: dict[str, tuple[datetime, list[dict]]] = {}

    def login_url(self, api_key: str) -> str:
        kite = self._client(api_key=api_key)
        return kite.login_url()

    def generate_session(self, *, api_key: str, api_secret: str, request_token: str) -> dict:
        kite = self._client(api_key=api_key)
        try:
            return kite.generate_session(request_token.strip(), api_secret=api_secret.strip())
        except Exception as exc:
            raise ZerodhaExecutionError(str(exc)) from exc

    def place_market_order(
        self,
        *,
        api_key: str,
        access_token: str,
        exchange: str,
        tradingsymbol: str,
        transaction_type: str,
        quantity: int,
        product_type: str,
        correlation_id: str,
    ) -> BrokerOrderResult:
        if quantity <= 0:
            raise ZerodhaExecutionError("Order quantity must be positive.")
        if not exchange or not tradingsymbol:
            raise ZerodhaExecutionError("Zerodha exchange and tradingsymbol are required.")
        kite = self._client(api_key=api_key, access_token=access_token)
        product = self._map_product(product_type)
        try:
            order_id = kite.place_order(
                variety=kite.VARIETY_REGULAR,
                exchange=exchange,
                tradingsymbol=tradingsymbol,
                transaction_type=transaction_type,
                quantity=int(quantity),
                product=product,
                order_type=kite.ORDER_TYPE_MARKET,
                validity=kite.VALIDITY_DAY,
                tag=self._normalize_tag(correlation_id),
            )
        except Exception as exc:
            raise ZerodhaExecutionError(str(exc)) from exc
        return BrokerOrderResult(
            ok=True,
            order_id=str(order_id or "") or None,
            order_status="PENDING",
            message="Request accepted by Zerodha Kite.",
            raw={"order_id": order_id, "exchange": exchange, "tradingsymbol": tradingsymbol, "product": product},
        )

    def resolve_fno_tradingsymbol(
        self,
        *,
        api_key: str,
        access_token: str,
        underlying: str,
        instrument_type: str,
        expiry: date | None,
        strike: int | float | None = None,
        option_type: str | None = None,
    ) -> ZerodhaInstrument:
        normalized_underlying = self._normalize_underlying(underlying)
        normalized_type = instrument_type.strip().upper()
        if normalized_type == "FUTSTK":
            normalized_type = "FUT"
        if normalized_type in {"CE", "PE"}:
            normalized_option = normalized_type
        else:
            normalized_option = (option_type or "").strip().upper()
        rows = self._instruments(api_key=api_key, access_token=access_token, exchange="NFO")
        candidates: list[ZerodhaInstrument] = []
        for row in rows:
            candidate = self._instrument_from_row(row)
            if candidate.exchange != "NFO":
                continue
            if self._normalize_underlying(candidate.name) != normalized_underlying:
                continue
            if normalized_type in {"FUT", "FUTSTK"} and candidate.instrument_type != "FUT":
                continue
            if normalized_type not in {"FUT", "FUTSTK"}:
                if candidate.instrument_type != normalized_option:
                    continue
                if strike is not None and int(round(candidate.strike)) != int(round(float(strike))):
                    continue
            if expiry is not None and candidate.expiry != expiry:
                continue
            candidates.append(candidate)
        if not candidates and expiry is not None:
            return self.resolve_fno_tradingsymbol(
                api_key=api_key,
                access_token=access_token,
                underlying=underlying,
                instrument_type=instrument_type,
                expiry=None,
                strike=strike,
                option_type=option_type,
            )
        if not candidates:
            detail = f"{underlying} {instrument_type}"
            if strike is not None:
                detail += f" {strike}"
            raise ZerodhaExecutionError(f"Could not resolve Zerodha NFO tradingsymbol for {detail}.")
        today = date.today()
        valid = [item for item in candidates if item.expiry is None or item.expiry >= today]
        chosen_pool = valid or candidates
        return sorted(chosen_pool, key=lambda item: item.expiry or date.max)[0]

    def resolve_atm_option_tradingsymbol(
        self,
        *,
        api_key: str,
        access_token: str,
        underlying: str,
        spot: float,
        option_type: str,
        expiry: date | None = None,
    ) -> ZerodhaInstrument:
        normalized_underlying = self._normalize_underlying(underlying)
        normalized_option = option_type.strip().upper()
        rows = self._instruments(api_key=api_key, access_token=access_token, exchange="NFO")
        candidates: list[ZerodhaInstrument] = []
        for row in rows:
            candidate = self._instrument_from_row(row)
            if candidate.exchange != "NFO":
                continue
            if self._normalize_underlying(candidate.name) != normalized_underlying:
                continue
            if candidate.instrument_type != normalized_option:
                continue
            if expiry is not None and candidate.expiry != expiry:
                continue
            candidates.append(candidate)
        if not candidates and expiry is not None:
            return self.resolve_atm_option_tradingsymbol(
                api_key=api_key,
                access_token=access_token,
                underlying=underlying,
                spot=spot,
                option_type=option_type,
                expiry=None,
            )
        if not candidates:
            raise ZerodhaExecutionError(f"Could not resolve Zerodha ATM option for {underlying} {option_type}.")
        today = date.today()
        valid = [item for item in candidates if item.expiry is None or item.expiry >= today]
        chosen_pool = valid or candidates
        return sorted(
            chosen_pool,
            key=lambda item: (
                abs(float(item.strike) - float(spot)),
                item.expiry or date.max,
                item.strike,
            ),
        )[0]

    def fetch_ltp(self, *, api_key: str, access_token: str, exchange: str, tradingsymbol: str) -> float:
        kite = self._client(api_key=api_key, access_token=access_token)
        instrument_key = f"{exchange}:{tradingsymbol}"
        try:
            payload = kite.ltp(instrument_key)
        except Exception as exc:
            raise ZerodhaExecutionError(str(exc)) from exc
        quote = payload.get(instrument_key)
        if not isinstance(quote, dict) or quote.get("last_price") in (None, ""):
            raise ZerodhaExecutionError(f"Zerodha did not return LTP for {instrument_key}.")
        return float(quote["last_price"])

    def _client(self, *, api_key: str, access_token: str | None = None):
        try:
            from kiteconnect import KiteConnect
        except Exception as exc:
            raise ZerodhaExecutionError("kiteconnect package is not installed. Run pip install -r requirements.txt.") from exc
        kite = KiteConnect(api_key=api_key)
        if access_token:
            kite.set_access_token(access_token)
        return kite

    def _instruments(self, *, api_key: str, access_token: str, exchange: str) -> list[dict]:
        cache_key = exchange.upper()
        with self._lock:
            cached = self._instrument_cache.get(cache_key)
            if cached and datetime.now() - cached[0] < timedelta(hours=6):
                return cached[1]
        kite = self._client(api_key=api_key, access_token=access_token)
        try:
            instruments = kite.instruments(exchange)
        except Exception as exc:
            raise ZerodhaExecutionError(str(exc)) from exc
        with self._lock:
            self._instrument_cache[cache_key] = (datetime.now(), instruments)
        return instruments

    def _instrument_from_row(self, row: dict) -> ZerodhaInstrument:
        raw_expiry = row.get("expiry")
        expiry = raw_expiry if isinstance(raw_expiry, date) else None
        if expiry is None and raw_expiry:
            try:
                expiry = date.fromisoformat(str(raw_expiry)[:10])
            except ValueError:
                expiry = None
        return ZerodhaInstrument(
            tradingsymbol=str(row.get("tradingsymbol") or "").strip(),
            exchange=str(row.get("exchange") or "").strip().upper(),
            instrument_token=self._as_int(row.get("instrument_token")),
            name=str(row.get("name") or "").strip(),
            expiry=expiry,
            strike=float(row.get("strike") or 0.0),
            lot_size=max(self._as_int(row.get("lot_size")) or 1, 1),
            instrument_type=str(row.get("instrument_type") or "").strip().upper(),
        )

    def _map_product(self, product_type: str) -> str:
        normalized = str(product_type or "INTRADAY").strip().upper()
        if normalized in {"CNC"}:
            return "CNC"
        if normalized in {"NRML", "NORMAL", "MARGIN"}:
            return "NRML"
        return "MIS"

    def _normalize_tag(self, value: str) -> str:
        tag = "".join(ch for ch in str(value or "") if ch.isalnum())[:20]
        return tag or "aisamosa"

    def _normalize_underlying(self, value: object) -> str:
        normalized = str(value or "").strip().upper()
        if normalized in {"NIFTY 50", "NIFTY50"}:
            return "NIFTY"
        return normalized

    def _as_int(self, value: object) -> int | None:
        try:
            return int(float(str(value).strip()))
        except (TypeError, ValueError):
            return None
