from __future__ import annotations

import io
from pathlib import Path

from pypdf import PdfReader

from app.schemas import RulebookUpdate


DEFAULT_RULEBOOK_PATH = Path(__file__).resolve().parents[2] / "docs" / "heuristic_sl_hunting_rulebook_v6.md"

FALLBACK_RULEBOOK = """# SL Hunting Rulebook

## Core Belief

Market first moves toward liquidity, trapped traders, and writer pain. Real intention becomes visible only after sweep, reclaim, rejection, or acceptance.

## Rule Set

`R1`: Mark previous day high, previous day low, and previous day close before session start.
`R2`: Use all intraday candles since morning, not only the latest candle.
`R3`: Combine chart structure with option-chain context when available. OI alone is never enough.
`R4`: Bullish CE setup needs a sell-side sweep plus reclaim by candle close.
`R5`: Bearish PE setup needs a buy-side sweep plus rejection by candle close.
`R6`: If confirmation is weak, mixed, or trapped inside fair value, prefer no trade.
`R7`: When no trade is open, the AI may arm a pending setup with a trigger and invalidation instead of forcing entry.
`R8`: Once a pending setup is armed, keep its trigger stable until triggered, replaced, or invalidated.
`R9`: After entry, the AI manages the open trade with hold, stop, target, and exit decisions.
`R10`: Exit when the defended zone is lost or the trap thesis is invalidated by clean opposing acceptance.
"""


def _load_default_rulebook() -> str:
    try:
        return DEFAULT_RULEBOOK_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return FALLBACK_RULEBOOK.strip()


DEFAULT_RULEBOOK = _load_default_rulebook()


class RulebookService:
    def __init__(self) -> None:
        self.rulebook_markdown = DEFAULT_RULEBOOK
        self.learning_log: list[str] = [
            f"Loaded default SL-hunting rulebook from {DEFAULT_RULEBOOK_PATH.name}.",
            "Learning mode is enabled for uploaded text or PDF documents.",
        ]

    def get_rulebook(self) -> str:
        return self.rulebook_markdown

    def extract_text(self, filename: str, content: bytes) -> str:
        suffix = Path(filename).suffix.lower()
        if suffix in {".md", ".txt", ".text"}:
            return self._decode_text(content)
        if suffix == ".pdf":
            reader = PdfReader(io.BytesIO(content))
            pages = [page.extract_text() or "" for page in reader.pages]
            return "\n".join(pages)
        raise ValueError("Supported document types are .txt, .md, and .pdf")

    def _decode_text(self, content: bytes) -> str:
        for encoding in ("utf-8-sig", "utf-16", "utf-16-le", "utf-16-be", "latin-1"):
            try:
                return content.decode(encoding)
            except UnicodeDecodeError:
                continue
        raise ValueError("Unable to decode the uploaded text file. Save it as UTF-8, UTF-16, or plain text.")

    def update_rulebook(self, update: RulebookUpdate, source_name: str) -> str:
        self.rulebook_markdown = update.proposed_markdown.strip()
        summary = f"Rulebook updated from {source_name}: {update.summary}"
        self.learning_log.insert(0, summary)
        return summary
