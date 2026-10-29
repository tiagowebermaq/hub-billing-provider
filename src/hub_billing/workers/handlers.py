"""Lógica dos workers como funções puras sobre envelopes, testáveis sem RabbitMQ."""
from collections.abc import Callable

from hub_billing.adapters.base import RawBill
from hub_billing.adapters.registry import get_adapter
from hub_billing.domain.grouping import group_bills
from hub_billing.domain.models import BatchStatus, Bill
from hub_billing.infra.idempotency import IdempotencyGuard
from hub_billing.infra.repository import Repository
from hub_billing.messaging.envelope import Envelope
from hub_billing.rental.client import RentalClient, RentalRejected

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


def store_bill(env: Envelope, repo: Repository) -> Envelope | None:
    """bill.normalized -> grava a conta. Sem imóvel vinculado avisa por webhook que ela precisa de revisão."""
    bill = Bill.model_validate(env.payload["bill"])
    if not repo.save_bill(bill) or bill.property_id is not None:
        return None
    return env.derive("bill.manual_review", "grouper", {"bill_id": bill.id, "provider": bill.provider,
                                                         "account_number": bill.account_number})


def build_batches(env: Envelope, bills: list[Bill]) -> list[Envelope]:
    """Fecha lotes por imóvel/contrato/competência e gera um batch.ready por lote."""
    return [env.derive("batch.ready", "grouper", {"batch": b.model_dump(mode="json"), "total": str(b.total)})
            for b in group_bills(bills, env.correlation_id)]


async def dispatch(env: Envelope, repo: Repository, client: RentalClient) -> Envelope | None:
    """batch.ready -> envia ao sistema de aluguéis e devolve o evento batch.sent ou batch.failed.

    Falhas temporárias (RentalUnavailable) sobem para o consumidor aplicar retry/DLQ.
    """
    batch = repo.get_batch(env.payload["batch_id"])
    if batch is None:
        return None
    if batch.status in (BatchStatus.SENT, BatchStatus.ACKED):
        return None
    try:
        ack = await client.send(batch, env.correlation_id)
    except RentalRejected as exc:
        repo.set_batch_status(batch.id, BatchStatus.FAILED, str(exc))
        return env.derive("batch.failed", "dispatcher", {"batch_id": batch.id, "error": str(exc)})
    repo.set_batch_status(batch.id, BatchStatus.ACKED)
    return env.derive("batch.sent", "dispatcher", {"batch_id": batch.id, "property_id": batch.property_id,
                                                   "reference_month": batch.reference_month, "total": str(batch.total),
                                                   "ack": ack})
