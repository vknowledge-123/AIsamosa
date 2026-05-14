from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from app.config import Settings
from app.schemas import CredentialSummary, FullAIProvider, OperatingMode


class CredentialStore:
    def __init__(self, path: str | Path | None = None) -> None:
        if path is None:
            path = Path(__file__).resolve().parents[1] / "data" / "credentials.json"
        self.path = Path(path)

    def load(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def save(
        self,
        *,
        client_id: str | None = None,
        access_token: str | None = None,
        openai_api_key: str | None = None,
        openai_model: str | None = None,
        deepseek_api_key: str | None = None,
        deepseek_model: str | None = None,
        full_ai_provider: str | None = None,
        operating_mode: str | None = None,
    ) -> None:
        payload = self.load()
        updated = False

        if client_id and client_id.strip():
            payload["client_id"] = client_id.strip()
            updated = True
        if access_token and access_token.strip():
            payload["access_token"] = access_token.strip()
            updated = True
        if openai_api_key and openai_api_key.strip():
            payload["openai_api_key"] = openai_api_key.strip()
            updated = True
        if openai_model and openai_model.strip():
            payload["openai_model"] = openai_model.strip()
            updated = True
        if deepseek_api_key and deepseek_api_key.strip():
            payload["deepseek_api_key"] = deepseek_api_key.strip()
            updated = True
        if deepseek_model and deepseek_model.strip():
            payload["deepseek_model"] = deepseek_model.strip()
            updated = True
        if full_ai_provider and full_ai_provider.strip():
            payload["full_ai_provider"] = full_ai_provider.strip().lower()
            updated = True
        if operating_mode and operating_mode.strip():
            payload["operating_mode"] = operating_mode.strip()
            updated = True

        if not updated:
            return

        payload["last_updated"] = datetime.now().isoformat()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def get_dhan_credentials(self, settings: Settings) -> tuple[str | None, str | None]:
        payload = self.load()
        client_id = payload.get("client_id") or settings.dhan_client_id
        access_token = payload.get("access_token") or settings.dhan_access_token
        return client_id, access_token

    def get_openai_settings(self, settings: Settings) -> tuple[str | None, str]:
        payload = self.load()
        api_key = payload.get("openai_api_key") or settings.openai_api_key
        model = payload.get("openai_model") or settings.openai_model
        return api_key, model

    def get_deepseek_settings(self, settings: Settings) -> tuple[str | None, str]:
        payload = self.load()
        api_key = payload.get("deepseek_api_key") or settings.deepseek_api_key
        model = payload.get("deepseek_model") or settings.deepseek_model
        return api_key, model

    def get_full_ai_provider(self, settings: Settings) -> FullAIProvider:
        payload = self.load()
        raw = str(payload.get("full_ai_provider") or settings.full_ai_provider or FullAIProvider.openai.value).strip().lower()
        if raw == FullAIProvider.deepseek.value:
            return FullAIProvider.deepseek
        return FullAIProvider.openai

    def get_operating_mode(self, settings: Settings) -> OperatingMode:
        payload = self.load()
        raw = str(payload.get("operating_mode") or settings.operating_mode or OperatingMode.full_ai.value).strip().lower()
        if raw == OperatingMode.heuristic.value:
            return OperatingMode.heuristic
        return OperatingMode.full_ai

    def summary(self, settings: Settings) -> CredentialSummary:
        payload = self.load()
        last_updated = None
        if payload.get("last_updated"):
            try:
                last_updated = datetime.fromisoformat(payload["last_updated"])
            except ValueError:
                last_updated = None
        return CredentialSummary(
            client_id=payload.get("client_id") or settings.dhan_client_id,
            dhan_access_token_saved=bool(payload.get("access_token") or settings.dhan_access_token),
            openai_api_key_saved=bool(payload.get("openai_api_key") or settings.openai_api_key),
            openai_model=payload.get("openai_model") or settings.openai_model,
            deepseek_api_key_saved=bool(payload.get("deepseek_api_key") or settings.deepseek_api_key),
            deepseek_model=payload.get("deepseek_model") or settings.deepseek_model,
            full_ai_provider=self.get_full_ai_provider(settings),
            operating_mode=self.get_operating_mode(settings),
            storage_path=str(self.path.resolve()),
            last_updated=last_updated,
        )
