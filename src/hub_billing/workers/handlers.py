"""Lógica dos workers como funções puras sobre envelopes, testáveis sem RabbitMQ."""
from collections.abc import Callable

import httpx

from hub_billing.adapters.base import RawBill, SessionExpired
from hub_billing.adapters.registry import get_adapter
from hub_billing.domain.grouping import group_bills
from hub_billing.domain.models import BatchStatus, Bill
from hub_billing.infra.idempotency import IdempotencyGuard
from hub_billing.infra.repository import AccountLink, Repository
from hub_billing.messaging.envelope import Envelope
from hub_billing.rental.client import RentalClient, RentalRejected

PropertyLookup = Callable[[str, str], AccountLink | None]


async def collect(env: Envelope, repo: Repository, http: httpx.AsyncClient,
                  options: dict[str, str] | None = None) -> list[Envelope]:
    """collect.request -> um collect.result por conta encontrada no portal.

    Sem sessão válida devolve só o evento collect.login_required (vai para os webhooks), sem retry: o login
    assistido precisa ser refeito por uma pessoa.
    """
    p = env.payload
    provider, distributor = p["provider"], p.get("distributor")
    stored = repo.load_session(provider, distributor)
    if stored is None and not distributor and (only := repo.only_session(provider)) is not None:
        distributor = only or None  # coleta sem distribuidora: usa a única sessão salva do portal
        stored = repo.load_session(provider, distributor)
    adapter = get_adapter(provider, distributor).use(http, **(options or {}))
    accounts = []
    try:
        if stored is None:
            raise SessionExpired(f"nenhuma sessão salva para {provider}: faça o login assistido")
        session = await adapter.resume(stored[0])
        repo.save_session(provider, distributor, session.data, stored[1])
        accounts = await adapter.list_accounts(session)
        if p.get("account_number"):
            accounts = [a for a in accounts if a.account_number == p["account_number"]]
        raws = [raw for account in accounts for raw in await adapter.fetch_bills(session, account, p.get("reference_month"))]
        repo.save_session(provider, distributor, session.data, stored[1])  # cookies renovados pelo portal na coleta
    except SessionExpired as exc:
        repo.record_run(env.message_id, provider, distributor, "login", message=str(exc))
        return [env.derive("collect.login_required", "collector",
                           {"provider": provider, "distributor": distributor, "reason": str(exc)})]
    except Exception as exc:
        repo.record_run(env.message_id, provider, distributor, "erro", len(accounts), message=_reason(exc))
        raise
    message = None if raws else (
        "nenhuma unidade encontrada na sessão do portal" if not accounts else "nenhuma conta em aberto encontrada")
    repo.record_run(env.message_id, provider, distributor, "ok", len(accounts), len(raws), message)
    return [env.derive("collect.result", "collector", {"raw": raw.model_dump(mode="json"), "distributor": distributor})
            for raw in raws]


def _reason(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"o portal respondeu HTTP {exc.response.status_code}"
    if isinstance(exc, httpx.HTTPError):
        return f"falha de rede ao falar com o portal: {exc.__class__.__name__}"
    return str(exc) or exc.__class__.__name__


async def normalize(env: Envelope, guard: IdempotencyGuard, lookup: PropertyLookup) -> Envelope | None:
    """collect.result -> bill.normalized. Devolve None para contas já processadas."""
    raw = RawBill.model_validate(env.payload["raw"])
    adapter = get_adapter(raw.provider, env.payload.get("distributor"))
    bill = adapter.to_canonical(raw).model_copy(update={"correlation_id": env.correlation_id})
    if not await guard.first_time(bill.business_key):
        return None
    link = lookup(bill.provider, bill.account_number)
    if link is not None:
        bill = link.apply(bill)
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
