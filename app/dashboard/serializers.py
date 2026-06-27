from __future__ import annotations

from typing import Protocol


class DashboardSummaryProvider(Protocol):
    def get_state_summary(self) -> dict:
        ...


def serialize_summary(provider: DashboardSummaryProvider) -> dict:
    return provider.get_state_summary()

