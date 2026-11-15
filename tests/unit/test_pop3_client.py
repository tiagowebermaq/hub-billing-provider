import poplib
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest

from hub_billing.adapters.email_inbox.adapter import MailboxConfig
from hub_billing.adapters.email_inbox.imap import ImapMailClient, MailboxUnavailable
from hub_billing.adapters.email_inbox.pop3 import Pop3MailClient
from hub_billing.core.config import Settings
from hub_billing.infra.repository import Repository
from hub_billing.workers import mailbox
from tests.unit.emails import make_email


class FakePop3:
    """Servidor POP3 em memória: mensagens por UIDL; DELE só vale no QUIT, como no protocolo."""

    error_proto = poplib.error_proto
    server: "FakePop3"

    def __init__(self, host: str, port: int, **kwargs: object) -> None:
        self.messages: dict[str, bytes] = dict(FakePop3.mailbox)
        self.marked: set[int] = set()
        self.quit_fails = FakePop3.quit_fails
        FakePop3.server = self

    mailbox: dict[str, bytes] = {}
    quit_fails = False

    def user(self, name: str) -> bytes:
        return b"+OK"

    def pass_(self, password: str) -> bytes:
        if password != "certa":
            raise poplib.error_proto("-ERR [AUTH] Authentication failed")
        return b"+OK"

    def _uids(self) -> list[str]:
        return list(self.messages)

    def uidl(self) -> tuple[bytes, list[bytes], int]:
        return b"+OK", [f"{i} {u}".encode() for i, u in enumerate(self._uids(), 1)], 0

    def retr(self, number: int) -> tuple[bytes, list[bytes], int]:
        return b"+OK", self.messages[self._uids()[number - 1]].split(b"\n"), 0

    def dele(self, number: int) -> bytes:
        self.marked.add(number)
        return b"+OK"

    def quit(self) -> bytes:
        if self.quit_fails:
            raise OSError("caiu antes do QUIT")
        uids = self._uids()
        for number in self.marked:
            FakePop3.mailbox.pop(uids[number - 1], None)
        return b"+OK"


@pytest.fixture(autouse=True)
def fake_pop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(poplib, "POP3_SSL", FakePop3)
    monkeypatch.setattr(poplib, "POP3", FakePop3)
    FakePop3.mailbox = {"antigo-1": make_email(message_id="<a1@x>"), "antigo-2": make_email(message_id="<a2@x>")}
    FakePop3.quit_fails = False


@pytest.fixture
def repo() -> Iterator[Repository]:
    r = Repository.from_url("sqlite+pysqlite:///:memory:")
    r.create_schema()
    yield r
    r.engine.dispose()


CFG = MailboxConfig(protocol="pop3", host="mail.webermaq.com", port=995, username="webermaq@webermaq.com",
                    password="certa", delete_after_days=10)


def run(repo: Repository, now: datetime | None = None, cfg: MailboxConfig = CFG) -> list[str]:
    client = Pop3MailClient(cfg, repo, now=now)
    client.open()
    try:
        uids = client.unseen(50)
        for uid in uids:
            assert client.fetch(uid).startswith(b"From:") or b"Message-ID" in client.fetch(uid)
            client.mark_processed(uid)
        return uids
    finally:
        client.close()


def test_le_as_antigas_e_as_novas_uma_vez_so(repo: Repository) -> None:
    assert run(repo) == ["antigo-1", "antigo-2"]  # faturas que já estavam na caixa também entram
    assert run(repo) == []
    FakePop3.mailbox["novo-1"] = make_email(message_id="<n1@x>")
    assert run(repo) == ["novo-1"]


def test_apaga_so_as_novas_depois_dos_dias(repo: Repository) -> None:
    run(repo)
    FakePop3.mailbox["novo-1"] = make_email(message_id="<n1@x>")
    run(repo)
    run(repo, now=datetime.now(UTC) + timedelta(days=9))
    assert "novo-1" in FakePop3.mailbox
    run(repo, now=datetime.now(UTC) + timedelta(days=11))
    assert set(FakePop3.mailbox) == {"antigo-1", "antigo-2"}  # as que já estavam nunca são apagadas
    state = repo.pop3_state(Pop3MailClient(CFG, repo).account)
    assert state is not None and "novo-1" not in state


def test_sem_dias_nao_apaga_nada(repo: Repository) -> None:
    cfg = CFG.model_copy(update={"delete_after_days": 0})
    run(repo, cfg=cfg)
    FakePop3.mailbox["novo-1"] = make_email(message_id="<n1@x>")
    run(repo, cfg=cfg)
    run(repo, now=datetime.now(UTC) + timedelta(days=400), cfg=cfg)
    assert "novo-1" in FakePop3.mailbox


def test_queda_antes_do_quit_tenta_apagar_de_novo(repo: Repository) -> None:
    run(repo)
    FakePop3.mailbox["novo-1"] = make_email(message_id="<n1@x>")
    run(repo)
    FakePop3.quit_fails = True
    run(repo, now=datetime.now(UTC) + timedelta(days=11))
    assert "novo-1" in FakePop3.mailbox  # servidor desfez a exclusão
    FakePop3.quit_fails = False
    assert run(repo, now=datetime.now(UTC) + timedelta(days=11)) == []
    assert "novo-1" not in FakePop3.mailbox


def test_caixa_vazia_na_primeira_conexao(repo: Repository) -> None:
    FakePop3.mailbox = {}
    assert run(repo) == []
    FakePop3.mailbox["novo-1"] = make_email(message_id="<n1@x>")
    run(repo)
    run(repo, now=datetime.now(UTC) + timedelta(days=11))
    assert FakePop3.mailbox == {}  # chegou depois de ligar o hub: pode apagar


def test_erros(repo: Repository) -> None:
    with pytest.raises(MailboxUnavailable, match="senha do e-mail recusados"):
        Pop3MailClient(CFG.model_copy(update={"password": "errada"}), repo).open()
    client = Pop3MailClient(CFG.model_copy(update={"use_ssl": False}), repo)
    with pytest.raises(MailboxUnavailable, match="não conectada"):
        client.unseen(1)
    client.open()
    with pytest.raises(MailboxUnavailable, match="não está mais na caixa"):
        client.fetch("sumiu")
    client.close()
    client.close()


async def test_poll_pelo_pop3_publica_os_emails(repo: Repository) -> None:
    sent: list[object] = []

    async def publish(exchange: str, key: str, env: object) -> None:
        sent.append(env)

    report = await mailbox.poll(repo, CFG, mailbox.client_factory(repo), publish)
    assert (report.fetched, report.new) == (2, 2) and len(repo.list_emails()) == 2


def test_fabrica_escolhe_o_protocolo(repo: Repository) -> None:
    make = mailbox.client_factory(repo)
    assert isinstance(make(CFG), Pop3MailClient)
    assert isinstance(make(CFG.model_copy(update={"protocol": "imap"})), ImapMailClient)
    env = Settings(_env_file=None, email_protocol="POP3", email_imap_host="mail.webermaq.com", email_imap_port=995,
                   email_delete_after_days=10)
    cfg = mailbox.resolve_config(repo, env)
    assert (cfg.protocol, cfg.port, cfg.delete_after_days) == ("pop3", 995, 10)
