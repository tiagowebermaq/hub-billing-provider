import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import fakeredis.aioredis
import pytest

from hub_billing.adapters.email_inbox.adapter import EmailAdapter, MailboxConfig, parse_message
from hub_billing.adapters.registry import load_builtin
from hub_billing.core.config import Settings
from hub_billing.infra.idempotency import IdempotencyGuard
from hub_billing.infra.repository import Repository
from hub_billing.messaging import topology
from hub_billing.messaging.envelope import Envelope
from hub_billing.workers import mailbox
from hub_billing.workers.handlers import normalize
from tests.unit.emails import FakeMailClient, make_email
from tests.unit.invoices import invoice_lines, make_barcode, make_pdf

load_builtin()
CONFIG = MailboxConfig(host="imap.teste", password="x")


def rge_pdf() -> bytes:
    return make_pdf(["RGE SUL", *invoice_lines(make_barcode("3", 15_000))])


@pytest.fixture
def repo() -> Iterator[Repository]:
    r = Repository.from_url("sqlite+pysqlite:///:memory:")
    r.create_schema()
    yield r
    r.engine.dispose()


class Sent(list[tuple[str, str, Envelope]]):
    async def __call__(self, exchange: str, key: str, env: Envelope) -> None:
        self.append((exchange, key, env))


async def test_poll_guarda_publica_e_so_depois_marca_como_lido(repo: Repository) -> None:
    box = FakeMailClient({"1": make_email(pdfs=[("f.pdf", rge_pdf())]), "2": make_email(message_id="<m2@x>")})
    sent = Sent()
    report = await mailbox.poll(repo, CONFIG, box, sent)
    assert (report.fetched, report.new, report.requeued) == (2, 2, 0)
    assert box.seen == {"1", "2"} and box.closed == 1
    assert [(ex, key, env.type) for ex, key, env in sent] == [(topology.EX_EMAIL, "email.received", "email.received")] * 2
    cids = {env.correlation_id for *_, env in sent}
    assert len(cids) == 2  # um Correlation ID por e-mail
    [first, *_] = sent
    stored = repo.load_email(first[2].payload["email_id"])
    assert stored is not None and stored["status"] == "recebido" and stored["correlation_id"] == first[2].correlation_id


async def test_mesmo_email_nao_entra_duas_vezes(repo: Repository) -> None:
    raw = make_email()
    sent = Sent()
    await mailbox.poll(repo, CONFIG, FakeMailClient({"1": raw}), sent)
    await mailbox.poll(repo, CONFIG, FakeMailClient({"9": raw}), sent)  # reenviado / lido de novo
    assert len(sent) == 1 and len(repo.list_emails()) == 1


async def test_falha_na_fila_deixa_o_email_nao_lido(repo: Repository) -> None:
    box = FakeMailClient({"1": make_email()})

    async def down(exchange: str, key: str, env: Envelope) -> None:
        raise ConnectionError("rabbit fora")

    with pytest.raises(ConnectionError):
        await mailbox.poll(repo, CONFIG, box, down)
    assert box.seen == set() and box.closed == 1  # volta na próxima verificação
    # auto-cura: o e-mail ficou guardado em "recebido" e é republicado depois de 15 min
    sent = Sent()
    later = datetime.now(UTC) + timedelta(minutes=16)
    report = await mailbox.poll(repo, CONFIG, box, sent, now=later)
    assert report.new == 0 and report.requeued == 1 and box.seen == {"1"}
    assert sent[0][2].producer == "mailbox.requeue"


def test_backoff_dobra_ate_o_teto() -> None:
    assert [mailbox.backoff_seconds(n) for n in (1, 2, 3, 4)] == [30, 60, 120, 240]
    assert mailbox.backoff_seconds(50) == mailbox.BACKOFF_MAX_SECONDS


async def test_check_registra_falhas_seguidas_e_recupera(repo: Repository) -> None:
    settings = Settings(_env_file=None)
    repo.save_mailbox_config(CONFIG.model_dump())
    sent = Sent()
    broken = FakeMailClient({}, fail_on="open")
    assert await mailbox.check(repo, settings, broken, sent) == 30
    assert await mailbox.check(repo, settings, broken, sent) == 60
    state = repo.mailbox_state()
    assert state["failures"] == 2 and "falha simulada" in state["last_error"] and state["from_panel"]
    assert await mailbox.check(repo, settings, FakeMailClient({}), sent) == CONFIG.poll_seconds
    state = repo.mailbox_state()
    assert state["failures"] == 0 and state["last_error"] is None and state["last_ok_at"] is not None


async def test_check_sem_configuracao_nao_conecta(repo: Repository) -> None:
    box = FakeMailClient({"1": make_email()})
    await mailbox.check(repo, Settings(_env_file=None), box, Sent())
    assert box.opened == 0 and "não configurada" in repo.mailbox_state()["last_error"]


async def test_check_sobrevive_a_erro_inesperado(repo: Repository) -> None:
    class Quebrado(FakeMailClient):
        def fetch(self, uid: str) -> bytes:
            raise RuntimeError("bug")

    repo.save_mailbox_config(CONFIG.model_dump())
    assert await mailbox.check(repo, Settings(_env_file=None), Quebrado({"1": b""}), Sent()) == 30
    assert "falha ao guardar" in repo.mailbox_state()["last_error"]


def test_config_do_env_quando_o_painel_nao_cadastrou(repo: Repository) -> None:
    settings = Settings(_env_file=None, email_imap_host="imap.env", email_password="p", email_pdf_passwords="111, 222")
    cfg = mailbox.resolve_config(repo, settings)
    assert (cfg.host, cfg.username, cfg.pdf_passwords, cfg.configured) == (
        "imap.env", "41u6u31@heimber.com.br", ["111", "222"], True)
    repo.save_mailbox_config(MailboxConfig(host="imap.painel", password="q").model_dump())
    assert mailbox.resolve_config(repo, settings).host == "imap.painel"


async def test_run_poller_acorda_com_verificar_agora(repo: Repository) -> None:
    repo.save_mailbox_config(CONFIG.model_copy(update={"poll_seconds": 3600}).model_dump())
    box = FakeMailClient({})
    wake = asyncio.Event()
    task = asyncio.create_task(mailbox.run_poller(repo, Settings(_env_file=None), box, Sent(), wake, cycles=2))
    for _ in range(50):
        await asyncio.sleep(0.01)
        if box.opened == 1:
            break
    wake.set()
    await asyncio.wait_for(task, 2)
    assert box.opened == 2


async def test_run_poller_nao_para_quando_o_banco_cai(repo: Repository, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    async def explode(*args: object) -> float:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("banco fora")
        return 0.0

    monkeypatch.setattr(mailbox, "check", explode)
    monkeypatch.setattr(mailbox, "backoff_seconds", lambda n: 0.0)
    await asyncio.wait_for(mailbox.run_poller(repo, Settings(_env_file=None), FakeMailClient({}), Sent(),
                                              asyncio.Event(), cycles=2), 2)
    assert len(calls) == 2


def received(repo: Repository, raw: bytes, force: bool = False) -> Envelope:
    email = parse_message(raw)
    repo.save_email(email.id, email.message_id, email.sender, email.subject, email.received_at, raw, "cid-mail")
    return Envelope(type="email.received", producer="mailbox", correlation_id="cid-mail",
                    payload={"email_id": email.id, "force": force})


async def test_process_email_publica_collect_result_e_guarda_o_pdf(repo: Repository) -> None:
    env = received(repo, make_email(pdfs=[("fatura.pdf", rge_pdf())]))
    [out] = mailbox.process_email(env, repo, EmailAdapter())
    assert out.type == "collect.result" and out.correlation_id == "cid-mail" and out.causation_id == env.message_id
    assert mailbox.provider_routing_key(out) == "collect.result.cpfl"
    assert out.payload["distributor"] == "rge"
    assert out.payload["raw"]["payload"]["document_url"] == "/api/v1/documents/cpfl/40012345/2026-10"
    assert repo.load_document("cpfl:40012345:2026-10") is not None
    row = repo.list_emails()[0]
    assert (row["status"], row["provider"], row["bills"]) == ("processado", "cpfl", 1)
    # segue o fluxo normal: o normalizador aceita e mantém o correlation_id do e-mail
    guard = IdempotencyGuard(fakeredis.aioredis.FakeRedis(), 60)
    normalized = await normalize(out, guard, lambda p, a: None)
    assert normalized is not None and normalized.correlation_id == "cid-mail"
    # mensagem repetida (auto-cura) não reprocessa
    assert mailbox.process_email(env, repo, EmailAdapter()) == []


def test_process_email_ilegivel_vai_para_revisao(repo: Repository) -> None:
    env = received(repo, make_email(pdfs=[("f.pdf", b"lixo")]))
    [out] = mailbox.process_email(env, repo, EmailAdapter())
    assert out.type == "email.manual_review" and out.payload["problems"][0].startswith("f.pdf")
    assert repo.list_emails()[0]["status"] == "revisao"


def test_process_email_sem_fatura_e_ignorado_e_reprocessa_com_force(repo: Repository) -> None:
    raw = make_email(subject="Newsletter", body="oi")
    env = received(repo, raw)
    assert mailbox.process_email(env, repo, EmailAdapter()) == []
    assert repo.list_emails()[0]["status"] == "ignorado"
    assert mailbox.process_email(env, repo, EmailAdapter()) == []
    forced = env.model_copy(update={"payload": {**env.payload, "force": True}})
    mailbox.process_email(forced, repo, EmailAdapter())
    assert repo.list_emails()[0]["status"] == "ignorado"


def test_process_email_erro_inesperado_sobe_para_retry(repo: Repository) -> None:
    class Quebrado(EmailAdapter):
        def extract(self, email):  # type: ignore[no-untyped-def]
            raise RuntimeError("bug")

    env = received(repo, make_email())
    with pytest.raises(RuntimeError):
        mailbox.process_email(env, repo, Quebrado())
    assert repo.list_emails()[0]["status"] == "erro"
    missing = Envelope(type="email.received", producer="x", payload={"email_id": "nao-existe"})
    assert mailbox.process_email(missing, repo, EmailAdapter()) == []


def test_filas_de_email_tem_retry_e_dlq() -> None:
    names = {q.name for q in topology.MAIN_QUEUES}
    assert {"email.poll", "email.received"} <= names
    assert topology.retry_target("email.received", 1) == "email.received.retry.30s"
    assert "email.received.dlq" in {q.name for q in topology.dlq_queues()}
    assert topology.EXCHANGES[topology.EX_EMAIL] == "topic"
