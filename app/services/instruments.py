from __future__ import annotations

from dataclasses import dataclass

from app.schemas import InstrumentMode, InstrumentState


@dataclass(frozen=True)
class InstrumentSpec:
    mode: InstrumentMode
    label: str
    symbol: str
    security_id: str
    exchange_segment: str
    instrument_type: str
    supports_options: bool

    def to_state(self, lot_size: int) -> InstrumentState:
        return InstrumentState(
            mode=self.mode,
            label=self.label,
            symbol=self.symbol,
            security_id=self.security_id,
            exchange_segment=self.exchange_segment,
            instrument_type=self.instrument_type,
            supports_options=self.supports_options,
            lot_size=lot_size,
        )


NIFTY_INSTRUMENT = InstrumentSpec(
    mode=InstrumentMode.nifty,
    label="Nifty 50",
    symbol="NIFTY",
    security_id="13",
    exchange_segment="IDX_I",
    instrument_type="INDEX",
    supports_options=True,
)

SBIN_INSTRUMENT = InstrumentSpec(
    mode=InstrumentMode.stock,
    label="SBIN",
    symbol="SBIN",
    security_id="3045",
    exchange_segment="NSE_EQ",
    instrument_type="EQUITY",
    supports_options=False,
)

INSTRUMENTS: dict[InstrumentMode, InstrumentSpec] = {
    InstrumentMode.nifty: NIFTY_INSTRUMENT,
    InstrumentMode.stock: SBIN_INSTRUMENT,
}


def get_instrument_spec(mode: InstrumentMode | str | None) -> InstrumentSpec:
    normalized = InstrumentMode((mode or InstrumentMode.nifty).value if isinstance(mode, InstrumentMode) else (mode or InstrumentMode.nifty))
    return INSTRUMENTS[normalized]
