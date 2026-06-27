from __future__ import annotations

from collections.abc import Callable

from app.schemas import SimulatedTrade
from app.services.dhan_execution import BrokerOrderResult, DhanExecutionError, DhanExecutionService
from app.services.instruments import InstrumentSpec
from app.services.zerodha_execution import ZerodhaExecutionError, ZerodhaExecutionService


class OrderManager:
    """Routes normalized trade orders to the selected broker.

    This intentionally keeps the existing execution services underneath. It gives
    the engine one order-facing surface while we continue extracting behavior.
    """

    def __init__(
        self,
        *,
        dhan_execution: DhanExecutionService,
        zerodha_execution: ZerodhaExecutionService,
        dhan_credentials: Callable[[], tuple[str, str]],
        zerodha_credentials: Callable[[], tuple[str, str, str]],
        selected_broker: Callable[[], str],
        instrument_spec: Callable[[], InstrumentSpec],
    ) -> None:
        self._dhan_execution = dhan_execution
        self._zerodha_execution = zerodha_execution
        self._dhan_credentials = dhan_credentials
        self._zerodha_credentials = zerodha_credentials
        self._selected_broker = selected_broker
        self._instrument_spec = instrument_spec

    def place_market_order(
        self,
        *,
        trade: SimulatedTrade,
        transaction_type: str,
        quantity: int,
        correlation_id: str,
    ) -> BrokerOrderResult:
        broker = trade.broker_provider or self._selected_broker()
        if broker == "zerodha":
            api_key, _, access_token = self._zerodha_credentials()
            if not api_key or not access_token:
                raise ZerodhaExecutionError("Zerodha API key and access token are unavailable.")
            return self._zerodha_execution.place_market_order(
                api_key=api_key,
                access_token=access_token,
                exchange=trade.broker_exchange or "",
                tradingsymbol=trade.broker_tradingsymbol or "",
                transaction_type=transaction_type,
                quantity=quantity,
                product_type=trade.broker_product_type or "INTRADAY",
                correlation_id=correlation_id,
            )

        client_id, access_token = self._dhan_credentials()
        if not client_id or not access_token:
            raise DhanExecutionError("Dhan credentials are unavailable.")
        security_id = trade.option_security_id if trade.price_mode == "option" else trade.trade_security_id
        exchange_segment = trade.quote_exchange_segment or self._instrument_spec().exchange_segment
        if not security_id or not exchange_segment:
            raise DhanExecutionError("The execution contract could not be resolved.")
        return self._dhan_execution.place_market_order(
            client_id=client_id,
            access_token=access_token,
            security_id=security_id,
            exchange_segment=exchange_segment,
            transaction_type=transaction_type,
            quantity=quantity,
            product_type=trade.broker_product_type or "INTRADAY",
            correlation_id=correlation_id,
        )

