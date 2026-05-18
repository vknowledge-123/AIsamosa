from __future__ import annotations

from dataclasses import dataclass

from app.services.dhan_compat import create_dhan_client


class DhanExecutionError(RuntimeError):
    pass


@dataclass
class BrokerOrderResult:
    ok: bool
    order_id: str | None
    order_status: str | None
    message: str
    raw: dict


class DhanExecutionService:
    def place_market_order(
        self,
        *,
        client_id: str,
        access_token: str,
        security_id: str,
        exchange_segment: str,
        transaction_type: str,
        quantity: int,
        product_type: str,
        correlation_id: str,
    ) -> BrokerOrderResult:
        if quantity <= 0:
            raise DhanExecutionError("Order quantity must be positive.")
        client = create_dhan_client(client_id, access_token)
        response = client.place_order(
            security_id=str(security_id),
            exchange_segment=exchange_segment,
            transaction_type=transaction_type,
            quantity=int(quantity),
            order_type=client.MARKET,
            product_type=product_type,
            price=0.0,
            trigger_price=0.0,
            validity=client.DAY,
            tag=correlation_id,
        )
        ok, message = self._response_ok(response)
        payload = self._response_data(response)
        return BrokerOrderResult(
            ok=ok,
            order_id=str(payload.get("orderId") or payload.get("order_id") or "") or None,
            order_status=str(payload.get("orderStatus") or payload.get("status") or "") or None,
            message=message,
            raw=payload,
        )

    def activate_kill_switch(self, *, client_id: str, access_token: str, action: str) -> BrokerOrderResult:
        client = create_dhan_client(client_id, access_token)
        response = client.kill_switch(action)
        ok, message = self._response_ok(response)
        payload = self._response_data(response)
        return BrokerOrderResult(
            ok=ok,
            order_id=None,
            order_status=str(payload.get("killSwitchStatus") or "") or None,
            message=message,
            raw=payload,
        )

    def _response_ok(self, response: dict) -> tuple[bool, str]:
        status = str(response.get("status") or "").strip().lower()
        if status == "success":
            return True, "Request accepted by Dhan."
        remarks = response.get("remarks")
        if isinstance(remarks, dict):
            detail = remarks.get("error_message") or remarks.get("error_type") or remarks.get("error_code")
            if detail:
                return False, str(detail)
        return False, str(remarks or "Dhan request failed.")

    def _response_data(self, response: dict) -> dict:
        data = response.get("data")
        return data if isinstance(data, dict) else {}
