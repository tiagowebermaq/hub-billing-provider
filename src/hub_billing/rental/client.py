"""Envio dos lotes ao sistema de aluguéis.

O contrato abaixo é a proposta do hub (docs/integracao-locacao.md) até o sistema de aluguéis publicar o dele:
POST {rental_api_url} com o lote em JSON, Idempotency-Key = id do lote, Correlation ID e assinatura HMAC opcional.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

import httpx

from hub_billing.core.correlation import HEADER as CORRELATION_HEADER
from hub_billing.domain.models import BillingBatch
from hub_billing.webhooks.signing import SIGNATURE_HEADER, TIMESTAMP_HEADER, sign


class RentalUnavailable(RuntimeError):
    """Falha temporária (rede, timeout, 429 ou 5xx): a mensagem volta para a fila de retry."""


class RentalRejected(RuntimeError):
    """O sistema de aluguéis recusou o lote (4xx): não adianta repetir, vai para revisão."""


def to_payload(batch: BillingBatch) -> dict[str, Any]:
    return {
        "batch_id": batch.id,
        "property_id": batch.property_id,
        "contract_id": batch.contract_id,
        "reference_month": batch.reference_month,
        "total": str(batch.total),
        "bills": [{
            "bill_id": b.id,
            "provider": b.provider,
            "distributor": b.distributor,
            "utility_type": b.utility_type.value,
            "account_number": b.account_number,
            "due_date": b.due_date.isoformat(),
            "amount": str(b.amount),
            "consumption": None if b.consumption is None else str(b.consumption),
            "consumption_unit": b.consumption_unit,
            "barcode": b.barcode,
            "digitable_line": b.digitable_line,
            "pix_code": b.pix_code,
            "document_url": b.document_url,
        } for b in batch.bills],
    }


@dataclass
class RentalClient:
    http: httpx.AsyncClient
    url: str
    secret: str = ""
    timeout: float = 15.0

    async def send(self, batch: BillingBatch, correlation_id: str) -> dict[str, Any]:
        """Envia o lote. 2xx e 409 (lote já recebido) contam como aceito; devolve o corpo da resposta."""
        body = json.dumps(to_payload(batch), ensure_ascii=False).encode()
        headers = {"Content-Type": "application/json", "Idempotency-Key": batch.id, CORRELATION_HEADER: correlation_id}
        if self.secret:
            ts = int(time.time())
            headers |= {TIMESTAMP_HEADER: str(ts), SIGNATURE_HEADER: sign(self.secret, ts, body)}
        try:
            resp = await self.http.post(self.url, content=body, headers=headers, timeout=self.timeout)
        except httpx.HTTPError as exc:
            raise RentalUnavailable(f"{self.url}: {exc}") from exc
        if resp.status_code == 429 or resp.status_code >= 500:
            raise RentalUnavailable(f"{self.url}: HTTP {resp.status_code}")
        if resp.status_code >= 400 and resp.status_code != 409:
            raise RentalRejected(f"HTTP {resp.status_code}: {resp.text[:500]}")
        try:
            data = resp.json()
        except ValueError:
            data = {}
        return data if isinstance(data, dict) else {"response": data}
