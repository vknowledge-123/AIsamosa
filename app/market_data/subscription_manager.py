from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.services.instruments import InstrumentSpec


Subscription = tuple[Any, ...]


@dataclass(frozen=True)
class SubscriptionPlan:
    instruments: list[Subscription]
    security_to_subscription: dict[str, Subscription]
    security_to_symbol: dict[str, str]


class SubscriptionManager:
    """Builds broker-specific quote subscription plans without touching engine state."""

    def __init__(
        self,
        *,
        dhan_quote_builder: Callable[[str, str], Subscription],
        zerodha_quote_builder: Callable[[InstrumentSpec], Subscription],
        live_feed_security_id: Callable[[InstrumentSpec, str], str | None],
    ) -> None:
        self._dhan_quote_builder = dhan_quote_builder
        self._zerodha_quote_builder = zerodha_quote_builder
        self._live_feed_security_id = live_feed_security_id

    def quote_subscription(self, spec: InstrumentSpec, broker: str) -> Subscription:
        if broker == "zerodha":
            return self._zerodha_quote_builder(spec)
        return self._dhan_quote_builder(spec.security_id, spec.exchange_segment)

    def stock_watchlist_plan(
        self,
        *,
        specs: list[InstrumentSpec],
        broker: str,
        companion_spec: InstrumentSpec | None = None,
    ) -> SubscriptionPlan:
        instruments: list[Subscription] = []
        security_to_subscription: dict[str, Subscription] = {}
        security_to_symbol: dict[str, str] = {}
        for spec in specs:
            subscription = self.quote_subscription(spec, broker)
            instruments.append(subscription)
            feed_security_id = self._live_feed_security_id(spec, broker)
            if feed_security_id:
                security_to_subscription[feed_security_id] = subscription
                security_to_symbol[feed_security_id] = spec.symbol
        if companion_spec is not None and companion_spec.security_id:
            instruments.append(self.quote_subscription(companion_spec, broker))
        return SubscriptionPlan(
            instruments=instruments,
            security_to_subscription=security_to_subscription,
            security_to_symbol=security_to_symbol,
        )

    def single_instrument_plan(
        self,
        *,
        spec: InstrumentSpec,
        broker: str,
        companion_spec: InstrumentSpec | None = None,
    ) -> SubscriptionPlan:
        instruments = [self.quote_subscription(spec, broker)]
        if companion_spec is not None and companion_spec.security_id:
            instruments.append(self.quote_subscription(companion_spec, broker))
        return SubscriptionPlan(instruments=instruments, security_to_subscription={}, security_to_symbol={})
