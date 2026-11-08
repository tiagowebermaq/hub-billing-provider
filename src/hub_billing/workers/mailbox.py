"""Workers da caixa de faturas por e-mail.

- mailbox (leitor da caixa): a cada N minutos, ou quando o painel pede "Verificar agora" (fila email.poll), baixa os
  e-mails não lidos, guarda cada um no banco e publica email.received. Só marca o e-mail como lido depois que ele
  está guardado e na fila: se algo cair no meio, ele é lido de novo na próxima volta (sem duplicar, pelo Message-ID).
- mailreader (leitor das faturas): consome email.received, lê os PDFs com o adapter de e-mail e publica um
  collect.result por fatura, que segue pelo mesmo caminho da coleta nos portais.

Auto-cura: falha de conexão com a caixa espera cada vez mais (30 s, 1 min, 2 min... até 30 min) e tenta de novo,
sem derrubar o worker; e-mails parados em "recebido" (mensagem perdida na fila) são republicados sozinhos; erro
inesperado ao ler a fatura vai para retry (30 s, 5 min, 1 h) e depois para a DLQ, e o painel permite reprocessar.
"""
from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from hub_billing.adapters.email_inbox.adapter import EmailAdapter, MailboxConfig, parse_message
from hub_billing.adapters.email_inbox.imap import MailboxUnavailable, MailClient
from hub_billing.adapters.registry import get_adapter
from hub_billing.core import correlation
from hub_billing.core.config import Settings
from hub_billing.infra.repository import Repository
from hub_billing.messaging import topology
from hub_billing.messaging.envelope import Envelope
from hub_billing.workers.handlers import document_url

log = logging.getLogger(__name__)
Publish = Callable[[str, str, Envelope], Awaitable[None]]
ClientFactory = Callable[[MailboxConfig], MailClient]

POLL_LIMIT = 50
STALE_AFTER = timedelta(minutes=15)
BACKOFF_BASE_SECONDS = 30.0
BACKOFF_MAX_SECONDS = 1800.0


def resolve_config(repo: Repository, settings: Settings) -> MailboxConfig:
    """Cadastro do painel, se houver; senão o .env."""
    stored = repo.load_mailbox_config()
    if stored is not None:
        return MailboxConfig.model_validate(stored)
    return MailboxConfig(
        host=settings.email_imap_host, port=settings.email_imap_port, use_ssl=settings.email_imap_ssl,
        username=settings.email_username, password=settings.email_password, folder=settings.email_folder,
        processed_folder=settings.email_processed_folder, poll_seconds=settings.email_poll_seconds,
        pdf_passwords=[p.strip() for p in settings.email_pdf_passwords.split(",") if p.strip()])


def backoff_seconds(failures: int, base: float = BACKOFF_BASE_SECONDS, cap: float = BACKOFF_MAX_SECONDS) -> float:
    """Espera antes de tentar a caixa de novo depois de `failures` falhas seguidas (dobra a cada falha)."""
    return float(min(cap, base * 2 ** max(failures - 1, 0)))


@dataclass
class PollReport:
    fetched: int = 0
    new: int = 0
    requeued: int = 0


def _received_env(email_id: str, info: dict[str, Any], correlation_id: str, producer: str) -> Envelope:
    return Envelope(type="email.received", producer=producer, correlation_id=correlation_id,
                    payload={"email_id": email_id, **info})


async def poll(repo: Repository, config: MailboxConfig, factory: ClientFactory, publish: Publish,
               limit: int = POLL_LIMIT, now: datetime | None = None) -> PollReport:
    """Uma volta na caixa. Levanta MailboxUnavailable se não deu para falar com o servidor."""
    report = PollReport()
    client = factory(config)
    try:
        await asyncio.to_thread(client.open)
        for uid in await asyncio.to_thread(client.unseen, limit):
            raw = await asyncio.to_thread(client.fetch, uid)
            report.fetched += 1
            email = parse_message(raw)
            cid = correlation.new_id()  # cada e-mail é um fluxo próprio, rastreável até o lote
            info = {"message_id": email.message_id, "from": email.sender, "subject": email.subject}
            if repo.save_email(email.id, email.message_id, email.sender, email.subject, email.received_at, raw, cid):
                token = correlation.set(cid)
                try:
                    log.info("e-mail novo de %s: %s", email.sender, email.subject)
                    await publish(topology.EX_EMAIL, "email.received", _received_env(email.id, info, cid, "mailbox"))
                finally:
                    correlation.reset(token)
                report.new += 1
            await asyncio.to_thread(client.mark_processed, uid)
    finally:
        await asyncio.to_thread(client.close)
    report.requeued = await requeue_stale(repo, publish, now)
    return report


async def requeue_stale(repo: Repository, publish: Publish, now: datetime | None = None) -> int:
    """Republica e-mails que ficaram em 'recebido' (a mensagem não chegou ao leitor das faturas)."""
    stale = repo.stale_emails((now or datetime.now(UTC)) - STALE_AFTER)
    for row in stale:
        info = {"message_id": row["message_id"], "from": row["sender"], "subject": row["subject"]}
        cid = row["correlation_id"] or correlation.new_id()
        await publish(topology.EX_EMAIL, "email.received", _received_env(row["id"], info, cid, "mailbox.requeue"))
        repo.touch_email(row["id"])
        log.warning("e-mail %s parado em 'recebido': publicado de novo", row["id"],
                    extra={"extra_fields": {"correlation_id": cid}})
    return len(stale)


async def check(repo: Repository, settings: Settings, factory: ClientFactory, publish: Publish) -> float:
    """Verifica a caixa uma vez, registra a saúde e devolve quantos segundos esperar até a próxima volta."""
    config = resolve_config(repo, settings)
    if not config.configured:
        repo.record_mailbox_check(False, "caixa não configurada: cadastre o servidor IMAP e a senha no painel")
        await requeue_stale(repo, publish)
        return float(config.poll_seconds)
    try:
        report = await poll(repo, config, factory, publish)
    except MailboxUnavailable as exc:
        failures = repo.record_mailbox_check(False, str(exc))
        delay = backoff_seconds(failures)
        log.warning("caixa de e-mail indisponível (%s falhas seguidas): %s; nova tentativa em %.0f s",
                    failures, exc, delay)
        return delay
    except Exception as exc:  # noqa: BLE001 - banco ou fila fora: o worker não cai, tenta de novo com espera
        failures = repo.record_mailbox_check(False, f"falha ao guardar os e-mails: {exc}")
        log.exception("falha na verificação da caixa de e-mail")
        return backoff_seconds(failures)
    repo.record_mailbox_check(True)
    if report.fetched or report.requeued:
        log.info("caixa verificada: %s lidos, %s novos, %s republicados", report.fetched, report.new, report.requeued)
    return float(config.poll_seconds)


async def run_poller(repo: Repository, settings: Settings, factory: ClientFactory, publish: Publish,
                     wake: asyncio.Event, cycles: int | None = None) -> None:
    """Laço do leitor da caixa. `wake` (fila email.poll) antecipa a próxima volta. `cycles` limita nos testes."""
    done = 0
    failures = 0
    while cycles is None or done < cycles:
        try:
            delay = await check(repo, settings, factory, publish)
            failures = 0
        except Exception:  # noqa: BLE001 - nem o registro da falha deu (banco fora): espera e tenta de novo
            failures += 1
            delay = backoff_seconds(failures)
            log.exception("verificação da caixa falhou; nova tentativa em %.0f s", delay)
        done += 1
        if cycles is not None and done >= cycles:
            break
        wake.clear()
        try:
            await asyncio.wait_for(wake.wait(), delay * random.uniform(0.9, 1.1))  # noqa: S311 - jitter
        except TimeoutError:
            pass


def process_email(env: Envelope, repo: Repository, adapter: EmailAdapter) -> list[Envelope]:
    """email.received -> um collect.result por fatura achada no e-mail (e email.manual_review se nada deu certo).

    Fatura que não dá para ler não é retentada (é o mesmo arquivo): fica em "revisão" no painel. Erros
    inesperados sobem para o consumidor aplicar retry e DLQ.
    """
    email_id = env.payload["email_id"]
    stored = repo.load_email(email_id)
    if stored is None:
        log.warning("e-mail %s não encontrado no banco", email_id)
        return []
    if stored["status"] in ("processado", "ignorado") and not env.payload.get("force"):
        return []  # mensagem repetida (republicada pela auto-cura): já foi lida
    try:
        email = parse_message(stored["raw"])
        result = adapter.extract(email)
        outs: list[Envelope] = []
        for item in result.bills:
            raw = item.raw
            if item.document:
                repo.save_document(f"{raw.provider}:{raw.account_number}:{raw.reference_month}", item.document)
                raw.payload["document_url"] = document_url(raw)
            # conta que já veio pelo portal ganha o PDF e o que faltava; a repetida o normalizador descarta
            repo.complete_bill(get_adapter(raw.provider, item.distributor).to_canonical(raw))
            outs.append(env.derive("collect.result", "mailreader",
                                   {"raw": raw.model_dump(mode="json"), "distributor": item.distributor}))
    except Exception as exc:
        repo.set_email_status(email_id, "erro", f"{exc.__class__.__name__}: {exc}")
        raise
    detail = "; ".join(result.problems) or None
    if result.bills:
        repo.set_email_status(email_id, "processado", detail, ", ".join(result.providers), len(result.bills))
    elif result.problems:
        repo.set_email_status(email_id, "revisao", detail)
        outs.append(env.derive("email.manual_review", "mailreader",
                               {"email_id": email_id, "from": email.sender, "subject": email.subject,
                                "problems": result.problems}))
    else:
        repo.set_email_status(email_id, "ignorado", "sem fatura em PDF nem linha digitável")
    return outs


def provider_routing_key(env: Envelope) -> str:
    return f"collect.result.{env.payload['raw']['provider']}"
