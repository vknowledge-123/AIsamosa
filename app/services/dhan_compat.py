from __future__ import annotations

try:  # pragma: no cover - depends on installed SDK version
    from dhanhq import DhanContext, dhanhq as ContextDhanAPI
except Exception:  # pragma: no cover - optional runtime dependency path
    DhanContext = None
    ContextDhanAPI = None

try:  # pragma: no cover - depends on installed SDK version
    from dhanhq import dhanhq as LegacyDhanAPI
except Exception:  # pragma: no cover - optional runtime dependency path
    LegacyDhanAPI = None


def create_dhan_client(client_id: str, access_token: str):
    if DhanContext is not None and ContextDhanAPI is not None:
        return ContextDhanAPI(DhanContext(client_id, access_token))
    if LegacyDhanAPI is not None:
        return LegacyDhanAPI(client_id, access_token)
    raise RuntimeError("dhanhq package is not available in this environment.")
