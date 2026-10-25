import json
import time
from dataclasses import dataclass

import httpx

from hub_billing.core.correlation import HEADER as CORRELATION_HEADER
from hub_billing.messaging.envelope import Envelope
from hub_billing.webhooks.signing import SIGNATURE_HEADER, TIMESTAMP_HEADER, sign


@dataclass(frozen=True)
class Subscription:
    id: str
    url: str
    secret: str
    events: frozenset[str]

    def wants(self, event: str) -> bool:
        return "*" in self.events or event in self.events


class DeliveryError(RuntimeError):
    pass


async def deliver(client: httpx.AsyncClient, sub: Subscription, env: Envelope, timeout: float = 10.0) -> int:
    """Entrega um evento a um assinante. Levanta DeliveryError para o consumidor aplicar retry/DLQ."""
    body = json.dumps({"id": env.message_id, "type": env.type, "occurred_at": env.occurred_at.isoformat(),
                       "data": env.payload}, ensure_ascii=False, default=str).encode()
    ts = int(time.time())
    headers = {
        "Content-Type": "application/json",
        "X-Hub-Event": env.type,
        "X-Hub-Delivery": env.message_id,
        CORRELATION_HEADER: env.correlation_id,
        TIMESTAMP_HEADER: str(ts),
        SIGNATURE_HEADER: sign(sub.secret, ts, body),
    }
    try:
        resp = await client.post(sub.url, content=body, headers=headers, timeout=timeout)
    except httpx.HTTPError as exc:
        raise DeliveryError(f"{sub.url}: {exc}") from exc
    if not 200 <= resp.status_code < 300:
        raise DeliveryError(f"{sub.url}: HTTP {resp.status_code}")
    return resp.status_code
