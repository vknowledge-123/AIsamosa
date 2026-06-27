from __future__ import annotations


class ReplayEngine:
    """Facade for future replay isolation.

    Existing replay logic still lives in SimulationEngine. This class marks the
    extraction boundary without changing runtime behavior.
    """

