"""Lógica dos workers como funções puras sobre envelopes, testáveis sem RabbitMQ."""
import logging
import re
from collections.abc import Callable
from datetime import UTC, datetime

import httpx

from hub_billing.adapters.base import ProviderAccount, ProviderAdapter, ProviderSession, RawBill, SessionExpired
from hub_billing.adapters.registry import get_adapter
from hub_billing.domain.grouping import group_bills
from hub_billing.domain.models import BatchStatus, Bill
from hub_billing.infra.idempotency import IdempotencyGuard
from hub_billing.infra.repository import AccountLink, Repository
from hub_billing.messaging.envelope import Envelope
from hub_billing.rental.client import RentalClient, RentalRejected

log = logging.getLogger(__name__)
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
        for raw in raws:
            await _keep_document(adapter, session, raw, repo)
            repo.complete_bill(adapter.to_canonical(raw))  # contas que já estavam gravadas ganham o que faltava
        repo.save_session(provider, distributor, session.data, stored[1])  # cookies renovados pelo portal na coleta
        payments = [] if p.get("reference_month") else reconcile_payments(repo, provider, accounts, raws)
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
    return [*(env.derive("collect.result", "collector", {"raw": raw.model_dump(mode="json"), "distributor": distributor})
              for raw in raws),
            *(env.derive("bill.paid" if bill.paid_at else "bill.reopened", "collector", payment_summary(bill))
              for bill in payments)]


_PAID = re.compile(r"\b(paga|pago|quitad[ao]|baixad[ao])\b", re.I)  # "pagamento" não conta
_NOT = re.compile(r"\b(não|nao)\b", re.I)
_OVERDUE = re.compile(r"atras|vencid|d[ée]bito", re.I)


def _paid_text(text: object) -> bool:
    return isinstance(text, str) and bool(_PAID.search(text)) and not _NOT.search(text)


def _overdue_text(text: object) -> bool:
    return isinstance(text, str) and bool(_OVERDUE.search(text))


def reconcile_payments(repo: Repository, provider: str, accounts: list[ProviderAccount], raws: list[RawBill],
                       now: datetime | None = None) -> list[Bill]:
    """Confere as contas gravadas de cada UC/matrícula que o portal listou com as contas em aberto que ele mostrou.

    Conta gravada que sumiu da lista de abertas vira paga (paid_at = quando o hub notou); conta paga pelo portal que
    volta a aparecer em aberto volta para aberto. Só mexe nas UCs que o portal acompanha nesta coleta: conta que veio
    só por e-mail ou importação, de outra UC, fica como está. O que foi marcado à mão no painel nunca muda.
    O site novo da CPFL mostra uma conta só por UC (a "Conta Atual"): ali, só as competências anteriores à mostrada
    viram pagas, e nenhuma enquanto a mostrada estiver atrasada (ela pode ser a mais antiga de várias em aberto).
    """
    now = now or datetime.now(UTC)
    single = [a for a in accounts if "conta_atual" in a.extra]
    if single and all(a.extra["conta_atual"] is None for a in single) and len(single) == len(accounts) > 1:
        log.warning("%s: nenhuma UC com conta atual nesta coleta; pagamento não conferido (site mudou?)", provider)
        return []
    changed: list[Bill] = []
    for account in accounts:
        shown = {r.reference_month: r for r in raws if r.account_number == account.account_number}
        partial = "conta_atual" in account.extra
        current = max(shown.values(), key=lambda r: r.reference_month, default=None)
        log.info("%s %s: portal mostra em aberto %s", provider, account.account_number,
                 ", ".join(f"{r.reference_month} ({r.payload.get('status') or 'sem situação'})"
                           for r in shown.values()) or "nenhuma conta")
        for bill in repo.account_bills(provider, account.account_number):
            if bill.payment_source == "manual":
                continue
            raw = shown.get(bill.reference_month)
            if raw is not None:
                status = raw.payload.get("status")
                if bill.paid_at is None and _paid_text(status):
                    paid = repo.set_payment(bill.id, now, "portal")
                elif bill.paid_at is not None and not _paid_text(status):
                    paid = repo.set_payment(bill.id, None, None)  # pago pelo portal, mas voltou em aberto
                else:
                    continue
            elif bill.paid_at is not None:
                continue
            elif partial and current is not None and (
                    bill.reference_month > current.reference_month or _overdue_text(current.payload.get("status"))):
                continue
            else:
                paid = repo.set_payment(bill.id, now, "portal")
            if paid is not None:
                log.info("conta %s %s %s %s pelo portal", provider, bill.account_number, bill.reference_month,
                         "paga" if paid.paid_at else "reaberta")
                changed.append(paid)
    return changed


def payment_summary(bill: Bill) -> dict[str, object]:
    """Aviso bill.paid / bill.reopened: o resumo da conta com a situação de pagamento."""
    return {**bill_summary(bill), "payment_status": bill.payment_status.value,
            "paid_at": bill.paid_at.isoformat() if bill.paid_at else None, "payment_source": bill.payment_source}


def document_url(raw: RawBill) -> str:
    return f"/api/v1/documents/{raw.provider}/{raw.account_number}/{raw.reference_month}"


async def _keep_document(adapter: ProviderAdapter, session: ProviderSession, raw: RawBill, repo: Repository) -> None:
    """Baixa o PDF da conta e guarda no hub, para o painel oferecer o arquivo. Falha aqui não derruba a coleta."""
    try:
        content = await adapter.download_document(session, raw)
    except SessionExpired:
        raise
    except Exception as exc:  # noqa: BLE001 - portal fora do ar, PDF indisponível, adapter sem download
        log.warning("PDF de %s %s %s não baixado: %s", raw.provider, raw.account_number, raw.reference_month,
                    _reason(exc))
        return
    if content:
        repo.save_document(f"{raw.provider}:{raw.account_number}:{raw.reference_month}", content)
        raw.payload["document_url"] = document_url(raw)


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


def bill_summary(bill: Bill) -> dict[str, object]:
    """O que os sistemas vizinhos recebem no webhook: o essencial e o link para buscar a conta completa."""
    return {"bill_id": bill.id, "provider": bill.provider, "distributor": bill.distributor,
            "utility_type": bill.utility_type.value, "account_number": bill.account_number,
            "reference_month": bill.reference_month, "due_date": bill.due_date.isoformat(), "amount": str(bill.amount),
            "property_id": bill.property_id, "contract_id": bill.contract_id, "status": bill.status.value,
            "payment_status": bill.payment_status.value,
            "links": {"bill": f"/api/v1/bills/{bill.id}", "document": bill.document_url}}


def store_bill(env: Envelope, repo: Repository) -> list[Envelope]:
    """bill.normalized -> grava a conta e avisa os sistemas vizinhos (webhook bill.stored) para buscarem os dados.

    Sem imóvel vinculado sai também bill.manual_review. Conta repetida não gera aviso.
    """
    bill = Bill.model_validate(env.payload["bill"])
    if not repo.save_bill(bill):
        return []
    outs = [env.derive("bill.stored", "grouper", bill_summary(bill))]
    if bill.property_id is None:
        outs.append(env.derive("bill.manual_review", "grouper", {"bill_id": bill.id, "provider": bill.provider,
                                                                  "account_number": bill.account_number}))
    return outs


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
