from __future__ import annotations

import threading
from datetime import datetime
import json

from openai import OpenAI
from pydantic import BaseModel

from app.config import Settings
from app.schemas import FullAIProvider, OperatingMode, RulebookUpdate, StrategyContext, TradeAction, TradeDecision


class RulebookExtraction(BaseModel):
    summary: str
    extracted_rules: list[str]
    conflicts: list[str]
    proposed_markdown: str


class RulebookChunkExtraction(BaseModel):
    summary: str
    extracted_rules: list[str]
    conflicts: list[str]


class AIDecisionService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.enabled = False
        self.api_key = settings.openai_api_key
        self.model = settings.openai_model
        self.provider = FullAIProvider.openai
        self.base_url: str | None = None
        self._status_lock = threading.Lock()
        self._last_rulebook_status = {
            "mode": "idle",
            "message": "No rulebook AI job has run yet.",
            "updated_at": None,
        }
        self._last_decision_status = {
            "mode": "idle",
            "message": "No AI trading decision has been evaluated yet.",
            "updated_at": None,
        }
        self.configure(
            provider=FullAIProvider.openai,
            api_key=self.api_key,
            model=self.model,
        )

    def configure(
        self,
        *,
        provider: FullAIProvider,
        api_key: str | None,
        model: str | None,
    ) -> None:
        self.provider = provider
        self.api_key = (api_key or "").strip() or None
        self.model = (model or "").strip()
        self.base_url = "https://api.deepseek.com" if provider == FullAIProvider.deepseek else None
        self.enabled = bool(self.api_key and self.model)

    def health(self) -> dict:
        if not self.enabled:
            return {
                "enabled": False,
                "reachable": False,
                "model": self.model,
                "provider": self.provider.value,
                "model_available": False,
                "message": f"AI is disabled because the {self.provider.value} API key or model is missing.",
                "last_rulebook_status": self._last_rulebook_status,
                "last_decision_status": self._last_decision_status,
            }

        return {
            "enabled": True,
            "reachable": True,
            "model": self.model,
            "provider": self.provider.value,
            "model_available": True,
            "message": (
                (
                    f"OpenAI is configured and model {self.model} is ready to use. "
                    "Live validation happens on rulebook and decision API calls."
                )
                if self.provider == FullAIProvider.openai
                else (
                    f"DeepSeek is configured and model {self.model} is ready to use. "
                    "Live validation happens on rulebook and decision API calls."
                )
            ),
            "last_rulebook_status": self._last_rulebook_status,
            "last_decision_status": self._last_decision_status,
        }

    def _client(self, timeout: float) -> OpenAI:
        if not self.api_key:
            raise RuntimeError(f"{self.provider.value} API key is not configured.")
        return OpenAI(api_key=self.api_key, base_url=self.base_url, timeout=timeout)

    def _responses_parse(
        self,
        *,
        prompt: str,
        schema_model: type[BaseModel],
        system_prompt: str,
        timeout: float = 90.0,
    ) -> BaseModel:
        if not self.api_key:
            raise RuntimeError("OpenAI API key is not configured.")

        client = self._client(timeout)
        response = client.responses.parse(
            model=self.model,
            instructions=system_prompt,
            input=prompt,
            text_format=schema_model,
        )
        parsed = getattr(response, "output_parsed", None)
        if parsed is None:
            raise RuntimeError("OpenAI returned no structured output.")
        return parsed

    def _deepseek_json_parse(
        self,
        *,
        prompt: str,
        schema_model: type[BaseModel],
        system_prompt: str,
        timeout: float = 90.0,
    ) -> BaseModel:
        client = self._client(timeout)
        response = client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": f"{system_prompt}\nReturn valid json only."},
                {"role": "user", "content": f"Respond in json matching this schema.\n{prompt}"},
            ],
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content or ""
        if not content.strip():
            raise RuntimeError("DeepSeek returned empty content.")
        return schema_model.model_validate(json.loads(content))

    def _structured_parse(
        self,
        *,
        prompt: str,
        schema_model: type[BaseModel],
        system_prompt: str,
        timeout: float = 90.0,
    ) -> BaseModel:
        if self.provider == FullAIProvider.deepseek:
            return self._deepseek_json_parse(
                prompt=prompt,
                schema_model=schema_model,
                system_prompt=system_prompt,
                timeout=timeout,
            )
        return self._responses_parse(
            prompt=prompt,
            schema_model=schema_model,
            system_prompt=system_prompt,
            timeout=timeout,
        )

    def _extract_candidate_rules(self, source_text: str, limit: int = 12) -> list[str]:
        return [
            line.strip("- ").strip()
            for line in source_text.splitlines()
            if line.strip() and len(line.split()) > 4
        ][:limit]

    def _chunk_rulebook_source(self, source_text: str, max_chars: int = 2200, max_lines: int = 28) -> list[str]:
        lines = [line.strip() for line in source_text.splitlines() if line.strip()]
        if not lines:
            return []

        chunks: list[str] = []
        current: list[str] = []
        current_chars = 0
        for line in lines:
            line_len = len(line) + 1
            if current and (current_chars + line_len > max_chars or len(current) >= max_lines):
                chunks.append("\n".join(current))
                current = []
                current_chars = 0
            current.append(line)
            current_chars += line_len
        if current:
            chunks.append("\n".join(current))
        return chunks

    def _merge_unique_rules(self, rules: list[str], limit: int = 24) -> list[str]:
        merged: list[str] = []
        seen: set[str] = set()
        for rule in rules:
            normalized = " ".join(rule.split()).strip().lower()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            merged.append(" ".join(rule.split()).strip())
            if len(merged) >= limit:
                break
        return merged

    def _summarize_exception(self, exc: Exception) -> str:
        detail = str(exc).strip()
        if detail:
            return f"{type(exc).__name__}: {detail}"
        return type(exc).__name__

    def _serialize_candle(self, candle) -> list[str | float]:
        return [
            candle.timestamp.strftime("%Y-%m-%d %H:%M"),
            round(candle.open, 2),
            round(candle.high, 2),
            round(candle.low, 2),
            round(candle.close, 2),
            round(candle.volume, 2),
        ]

    def _serialize_candles(self, candles: list) -> list[list[str | float]]:
        return [self._serialize_candle(candle) for candle in candles]

    def _build_rulebook_context(self, rulebook_markdown: str, max_chars: int = 12000) -> str:
        text = rulebook_markdown.strip()
        if len(text) <= max_chars:
            return text

        head_size = max_chars // 2
        tail_size = max_chars - head_size
        return (
            f"{text[:head_size].rstrip()}\n\n"
            "[... middle of rulebook omitted for length, latest learned notes kept below ...]\n\n"
            f"{text[-tail_size:].lstrip()}"
        )

    def _record_rulebook_status(self, mode: str, message: str) -> None:
        with self._status_lock:
            self._last_rulebook_status = {
                "mode": mode,
                "message": message,
                "updated_at": datetime.now().isoformat(),
            }

    def _record_decision_status(self, mode: str, message: str) -> None:
        with self._status_lock:
            self._last_decision_status = {
                "mode": mode,
                "message": message,
                "updated_at": datetime.now().isoformat(),
            }

    def _fallback_rulebook_update(
        self,
        *,
        current_rulebook: str,
        source_text: str,
        summary: str,
        suffix: str = "",
        extracted_rules: list[str] | None = None,
    ) -> RulebookUpdate:
        extracted = extracted_rules if extracted_rules is not None else self._extract_candidate_rules(source_text)
        merged = current_rulebook.strip()
        if extracted:
            merged += "\n\n## Learned Notes\n\n" + "\n".join(f"- {rule}" for rule in extracted)
        final_summary = f"{summary}{suffix}".strip()
        self._record_rulebook_status("fallback", final_summary)
        return RulebookUpdate(
            summary=final_summary,
            proposed_markdown=merged,
            extracted_rules=extracted,
            conflicts=[],
        )

    def propose_rulebook_update(self, current_rulebook: str, source_text: str) -> RulebookUpdate:
        source_chunks = self._chunk_rulebook_source(source_text)
        chunked_suffix = (
            f" The uploaded document was chunked before sending to the {self.provider.value.title()} model for faster processing."
            if len(source_chunks) > 1
            else ""
        )

        if not source_chunks:
            result = RulebookUpdate(
                summary="The uploaded document did not contain readable text lines.",
                proposed_markdown=current_rulebook,
                extracted_rules=[],
                conflicts=[],
            )
            self._record_rulebook_status("empty", result.summary)
            return result

        if not self.enabled:
            return self._fallback_rulebook_update(
                current_rulebook=current_rulebook,
                source_text=source_text,
                summary="Added uploaded notes to the learned-notes section using fallback extraction.",
                suffix=chunked_suffix,
            )

        try:
            chunk_results: list[RulebookChunkExtraction] = []
            chunk_failures: list[str] = []
            for index, chunk in enumerate(source_chunks, start=1):
                prompt = f"""
Chunk {index} of {len(source_chunks)}:
{chunk}
"""
                try:
                    parsed_chunk = self._structured_parse(
                        prompt=prompt,
                        schema_model=RulebookChunkExtraction,
                        system_prompt=(
                            "You extract SL-hunting trading rules from one chunk of a document. "
                            "Return concise rules, conflicts, and a one-sentence summary. "
                            "Return only structured output matching the schema."
                        ),
                        timeout=30.0,
                    )
                    chunk_results.append(parsed_chunk)
                except Exception as exc:
                    chunk_failures.append(f"chunk {index}: {self._summarize_exception(exc)}")

            if not chunk_results:
                failure_note = chunk_failures[0] if chunk_failures else f"No chunk could be processed by {self.provider.value}."
                return self._fallback_rulebook_update(
                    current_rulebook=current_rulebook,
                    source_text=source_text,
                    summary=f"{self.provider.value.title()} chunk processing failed ({failure_note}), so fallback extraction was used.",
                    suffix=chunked_suffix,
                )

            aggregated_rules = self._merge_unique_rules(
                [rule for chunk in chunk_results for rule in chunk.extracted_rules]
            )
            aggregated_conflicts = self._merge_unique_rules(
                [conflict for chunk in chunk_results for conflict in chunk.conflicts],
                limit=12,
            )
            chunk_summaries = "\n".join(f"- {chunk.summary}" for chunk in chunk_results)
            synthesis_prompt = f"""
Current rulebook:
{current_rulebook}

Extracted chunk summaries:
{chunk_summaries}

Extracted rules:
{aggregated_rules}

Detected conflicts:
{aggregated_conflicts}
"""
            try:
                parsed = self._structured_parse(
                    prompt=synthesis_prompt,
                    schema_model=RulebookExtraction,
                    system_prompt=(
                        "You update a trading rulebook for a paper-trading simulator. "
                        "Merge only clear, non-contradictory knowledge from extracted chunk rules. "
                        "Return only structured output matching the schema."
                    ),
                    timeout=45.0,
                )
                failure_suffix = ""
                if chunk_failures:
                    failure_suffix = f" Some chunks still fell back or were skipped: {'; '.join(chunk_failures[:2])}."
                summary = f"{parsed.summary}{chunked_suffix}{failure_suffix}".strip()
                self._record_rulebook_status("ai", summary)
                return RulebookUpdate(
                    summary=summary,
                    proposed_markdown=parsed.proposed_markdown,
                    extracted_rules=self._merge_unique_rules(parsed.extracted_rules or aggregated_rules),
                    conflicts=self._merge_unique_rules(parsed.conflicts or aggregated_conflicts, limit=12),
                )
            except Exception as exc:
                return self._fallback_rulebook_update(
                    current_rulebook=current_rulebook,
                    source_text=source_text,
                    summary=(
                        f"{self.provider.value.title()} extracted chunk rules but final synthesis failed "
                        f"({self._summarize_exception(exc)}), so fallback extraction was used."
                    ),
                    suffix=chunked_suffix,
                    extracted_rules=aggregated_rules or self._extract_candidate_rules(source_text),
                )
        except Exception as exc:
            return self._fallback_rulebook_update(
                current_rulebook=current_rulebook,
                source_text=source_text,
                summary=f"{self.provider.value.title()} rulebook processing failed ({self._summarize_exception(exc)}), so fallback extraction was used.",
                suffix=chunked_suffix,
            )

    def _full_ai_fallback_decision(self, context: StrategyContext, message: str) -> TradeDecision:
        if context.active_trade:
            return TradeDecision(
                action=TradeAction.hold,
                confidence=0.05,
                reason=message,
                decision_source="full-ai-fallback",
                strike=context.active_trade.strike,
                option_type=context.active_trade.option_type,
                target_option_price=context.active_trade.target_option_price,
                stop_option_price=context.active_trade.stop_option_price,
            )
        return TradeDecision(
            action=TradeAction.no_trade,
            confidence=0.0,
            reason=message,
            decision_source="full-ai-fallback",
        )

    def decide(
        self,
        context: StrategyContext,
        heuristic_decision: TradeDecision,
        operating_mode: OperatingMode,
    ) -> TradeDecision:
        if operating_mode in {OperatingMode.heuristic, OperatingMode.heuristic_advance}:
            if operating_mode == OperatingMode.heuristic and heuristic_decision.decision_source == "heuristic":
                heuristic_decision.decision_source = "heuristic"
            self._record_decision_status(
                operating_mode.value,
                f"{operating_mode.value} mode is active, so AI trading logic was skipped.",
            )
            return heuristic_decision

        if not self.enabled:
            message = f"Full AI mode is active, but the {self.provider.value} API key or model is missing."
            self._record_decision_status("full-ai-fallback", message)
            return self._full_ai_fallback_decision(context, message)

        rulebook_context = self._build_rulebook_context(context.rulebook_markdown)
        session_candles = self._serialize_candles(context.session_candles)
        previous_day_candles = self._serialize_candles(context.previous_day_candles)
        recent_candles = self._serialize_candles(context.recent_candles)

        prompt = f"""
Strategy rules:
{rulebook_context}

Current market context:
instrument={context.instrument.model_dump(mode="json")}
decision_candle={context.current_candle.model_dump(mode="json")}
forming_live_candle={context.live_current_candle.model_dump(mode="json") if context.live_current_candle else None}
market_structure={context.market_structure}
previous_day_candles={previous_day_candles}
session_candles_since_open={session_candles}
recent_candles={recent_candles}
previous_day={context.previous_day.model_dump()}
liquidity_zones={[zone.model_dump() for zone in context.liquidity_zones]}
operator_zones={[zone.model_dump() for zone in context.operator_zones]}
signal_events={[event.model_dump(mode='json') for event in context.signal_events]}
pending_setup={context.pending_setup.model_dump(mode='json') if context.pending_setup else None}
active_trade={context.active_trade.model_dump(mode='json') if context.active_trade else None}
heuristic_suggestion={heuristic_decision.model_dump(mode='json')}
instruction=You must reason from the full session narrative, not from only the latest candle. Always use previous-day context, all available intraday candles since morning, recent candles for microstructure, and the updated SL-hunting rulebook before deciding. If there is an existing pending_setup, treat it as locked trader memory and do not silently move its reclaim or trigger level. Use pending_setup_action=KEEP to continue the same setup, REPLACE only when the old setup is structurally invalid or obsolete, and INVALIDATE when it should be cancelled. If you want to wait for a future reclaim or break trigger, return action=NO_TRADE and fill the pending setup fields so the engine can persist that setup. Use HOLD, UPDATE_STOP, UPDATE_TARGET, and EXIT only when an active_trade already exists. If there is no active_trade and you are still waiting, use NO_TRADE instead of HOLD. Prefer NO_TRADE only when the full session structure still does not confirm a trap, reclaim, rejection, continuation, or exit thesis. If instrument.supports_options is true, use the simulator strike rule and CE or PE option logic. If instrument.supports_options is false, still use ENTER_CALL for bullish long-stock entries and ENTER_PUT for bearish short-stock entries; in that case the stop_option_price and target_option_price fields represent stock spot stop and target prices.
"""
        try:
            parsed = self._structured_parse(
                prompt=prompt,
                schema_model=TradeDecision,
                system_prompt=(
                    "You are the full-AI trading decision engine for an SL-hunting paper-trading simulator. "
                    "Read the provided SL-hunting rulebook and market context carefully. "
                    "You must analyze chart structure across the whole session, including liquidity sweeps, previous-day levels, "
                    "intraday high/low structure, and recent confirmation candles. "
                    "When a pending setup is already present, preserve that setup memory unless you explicitly replace or invalidate it. "
                    "Only use HOLD, UPDATE_STOP, UPDATE_TARGET, or EXIT when an active trade exists; otherwise use NO_TRADE for waiting states. "
                    "For cash-stock mode, map ENTER_CALL to bullish long stock and ENTER_PUT to bearish short stock, "
                    "and use the stop_option_price and target_option_price fields as spot stop and target prices. "
                    "Do not base the decision on only the latest candle. "
                    "Return only structured output matching the schema."
                ),
                timeout=35.0,
            )
            parsed.decision_source = f"full-ai-{self.provider.value}"
            self._record_decision_status("full-ai", f"{self.provider.value.title()} trading decision succeeded with action {parsed.action}.")
            return parsed
        except Exception as first_exc:
            retry_prompt = f"""
Rules context:
{self._build_rulebook_context(context.rulebook_markdown, max_chars=5000)}

Compact market context:
instrument={context.instrument.model_dump(mode="json")}
decision_candle={context.current_candle.model_dump(mode="json")}
forming_live_candle={context.live_current_candle.model_dump(mode="json") if context.live_current_candle else None}
market_structure={context.market_structure}
session_candle_count={len(context.session_candles)}
session_candles_since_open_tail={session_candles[-60:]}
recent_candles={recent_candles}
previous_day={context.previous_day.model_dump()}
signal_events={[event.model_dump(mode='json') for event in context.signal_events]}
pending_setup={context.pending_setup.model_dump(mode='json') if context.pending_setup else None}
active_trade={context.active_trade.model_dump(mode='json') if context.active_trade else None}
heuristic_suggestion={heuristic_decision.model_dump(mode='json')}
"""
            try:
                parsed_retry = self._structured_parse(
                    prompt=retry_prompt,
                    schema_model=TradeDecision,
                    system_prompt=(
                        "You are the full-AI trading decision engine for an SL-hunting paper-trading simulator. "
                        "Respect any existing pending setup memory unless you explicitly replace or invalidate it. "
                        "Only use HOLD, UPDATE_STOP, UPDATE_TARGET, or EXIT when an active trade exists; otherwise use NO_TRADE for waiting states. "
                        "For cash-stock mode, map ENTER_CALL to bullish long stock and ENTER_PUT to bearish short stock, "
                        "and use stop_option_price and target_option_price as spot stop and target prices. "
                        "Even in retry mode, use the provided market structure summary and session tail to reason about the full day context. "
                        "Use the compact context and return only structured output matching the schema."
                    ),
                    timeout=20.0,
                )
                parsed_retry.decision_source = f"full-ai-{self.provider.value}-retry"
                self._record_decision_status("full-ai-retry", f"{self.provider.value.title()} retry succeeded with action {parsed_retry.action}.")
                return parsed_retry
            except Exception as second_exc:
                message = (
                    f"Full AI mode could not get a usable {self.provider.value.title()} decision. "
                    f"Primary error: {self._summarize_exception(first_exc)}. "
                    f"Retry error: {self._summarize_exception(second_exc)}."
                )
                self._record_decision_status("full-ai-fallback", message)
                return self._full_ai_fallback_decision(context, message)
