"""Lógica dos workers como funções puras sobre envelopes, testáveis sem RabbitMQ."""
from collections.abc import Callable

from hub_billing.adapters.base import RawBill
from hub_billing.adapters.registry import get_adapter
from hub_billing.domain.grouping import group_bills
from hub_billing.domain.models import Bill
from hub_billing.infra.idempotency import IdempotencyGuard
from hub_billing.messaging.envelope import Envelope

PropertyLookup = Callable[[str, str], tuple[str, str | None] | None]


async def normalize(env: Envelope, guard: IdempotencyGuard, lookup: PropertyLookup) -> Envelope | None:
    """collect.result -> bill.normalized. Devolve None para contas já processadas."""
    raw = RawBill.model_validate(env.payload["raw"])
    adapter = get_adapter(raw.provider, env.payload.get("distributor"))
    bill = adapter.to_canonical(raw).model_copy(update={"correlation_id": env.correlation_id})
    if not await guard.first_time(bill.business_key):
        return None
    link = lookup(bill.provider, bill.account_number)
    if link is not None:
        bill = bill.model_copy(update={"property_id": link[0], "contract_id": link[1]})
    return env.derive("bill.normalized", "normalizer", {"bill": bill.model_dump(mode="json")})


def build_batches(env: Envelope, bills: list[Bill]) -> list[Envelope]:
    """Fecha lotes por imóvel/contrato/competência e gera um batch.ready por lote."""
    return [env.derive("batch.ready", "grouper", {"batch": b.model_dump(mode="json"), "total": str(b.total)})
            for b in group_bills(bills, env.correlation_id)]
