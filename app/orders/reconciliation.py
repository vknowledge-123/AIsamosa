from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReconciliationResult:
    pending_quantity: int = 0
    filled_quantity: int = 0
    message: str = ""

