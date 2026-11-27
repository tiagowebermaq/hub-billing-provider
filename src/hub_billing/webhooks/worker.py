"""Worker de webhooks: avisa os sistemas vizinhos dos eventos do hub para que eles busquem os dados.

events.webhook (todos os eventos) -> uma mensagem webhook.delivery por assinante interessado. Cada entrega tem a sua
própria fila de retry (30 s, 5 min, 1 h) e DLQ: um sistema fora do ar não atrasa nem repete a entrega dos outros.
O cabeçalho X-Hub-Delivery é o mesmo em todas as tentativas, para o vizinho descartar repetidas.
"""
from __future__ import annotations

import logging

import httpx

from hub_billing.infra.repository import Repository
from hub_billing.messaging.envelope import Envelope
from hub_billing.webhooks.dispatcher import DeliveryError, Subscription, deliver

log = logging.getLogger(__name__)

EVENTS = {
    "bill.stored": "Conta gravada (a cada conta nova, de portal, e-mail ou importação)",
    "bill.manual_review": "Conta sem imóvel vinculado",
    "bill.paid": "Conta paga (sumiu das contas em aberto do portal, ou marcada como paga no painel)",
    "bill.reopened": "Conta que estava paga voltou a ficar em aberto",
    "batch.sent": "Lote enviado ao sistema de aluguéis",
    "batch.failed": "Lote recusado pelo sistema de aluguéis",
    "collect.login_required": "Portal pede login de novo",
    "email.manual_review": "E-mail com fatura que não deu para ler",
}


def delivery_id(event: Envelope, subscription_id: str) -> str:
    return f"{event.message_id}:{subscription_id}"


def fan_out(env: Envelope, repo: Repository) -> list[Envelope]:
    """Evento do hub -> uma entrega por assinante ativo que quer esse evento."""
    outs = []
    for sub in repo.list_subscriptions(only_active=True):
        if "*" in sub["events"] or env.type in sub["events"]:
            out = env.derive("webhook.delivery", "webhooks",
                             {"subscription_id": sub["id"], "event": env.model_dump(mode="json")})
            repo.record_delivery(delivery_id(env, sub["id"]), sub["id"], env.type, env.correlation_id, "pendente", 0)
            outs.append(out)
    return outs


async def deliver_one(env: Envelope, repo: Repository, http: httpx.AsyncClient, timeout: float = 10.0) -> None:
    """webhook.delivery -> POST assinado no sistema vizinho. Falha sobe para o consumidor aplicar retry/DLQ."""
    event = Envelope.model_validate(env.payload["event"])
    sub_id = env.payload["subscription_id"]
    found = [s for s in repo.list_subscriptions(with_secret=True, only_active=True) if s["id"] == sub_id]
    if not found:
        return  # assinatura removida ou pausada depois do evento
    did = delivery_id(event, sub_id)
    if repo.delivery_status(did) == "entregue":
        return
    row = found[0]
    sub = Subscription(row["id"], row["url"], row["secret"], frozenset(row["events"]))
    try:
        status = await deliver(http, sub, event, timeout)
    except DeliveryError as exc:
        repo.record_delivery(did, sub_id, event.type, event.correlation_id, "falhou", env.attempt, error=str(exc))
        log.warning("webhook %s para %s falhou (tentativa %s): %s", event.type, row["name"], env.attempt, exc)
        raise
    repo.record_delivery(did, sub_id, event.type, event.correlation_id, "entregue", env.attempt, http_status=status)
