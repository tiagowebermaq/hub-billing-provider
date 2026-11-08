import imaplib
import socket

import pytest

from hub_billing.adapters.email_inbox import imap
from hub_billing.adapters.email_inbox.adapter import MailboxConfig
from hub_billing.adapters.email_inbox.imap import ImapMailClient, MailboxUnavailable


class FakeImap:
    """Imita o imaplib.IMAP4_SSL o suficiente para o cliente."""

    error = imaplib.IMAP4.error
    last: "FakeImap"

    def __init__(self, host: str, port: int, **kwargs: object) -> None:
        self.host, self.port = host, port
        self.calls: list[tuple[str, ...]] = []
        self.capabilities = ("IMAP4REV1", "MOVE")
        self.login_error: Exception | None = None
        FakeImap.last = self

    def login(self, user: str, password: str) -> tuple[str, list[bytes]]:
        if password != "certa":
            raise imaplib.IMAP4.error("[AUTHENTICATIONFAILED] Invalid credentials")
        return "OK", [b"logged in"]

    def select(self, folder: str) -> tuple[str, list[bytes]]:
        return ("OK", [b"2"]) if folder == '"INBOX"' else ("NO", [b"no such folder"])

    def uid(self, command: str, *args: str) -> tuple[str, list[object]]:
        self.calls.append((command, *args))
        if command == "SEARCH":
            return "OK", [b"7 8"]
        if command == "FETCH":
            return "OK", [(b"7 (UID 7 BODY[] {5}", b"From: a@b\r\n\r\noi"), b")"]
        if command == "MOVE" and args[1] == '"quebrada"':
            raise OSError("caiu")
        return "OK", [b""]

    def logout(self) -> None:
        raise OSError("já caiu")


@pytest.fixture(autouse=True)
def fake_imap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(imaplib, "IMAP4_SSL", FakeImap)
    monkeypatch.setattr(imaplib, "IMAP4", FakeImap)


def cfg(**kw: object) -> MailboxConfig:
    return MailboxConfig.model_validate({"host": "imap.x", "password": "certa", **kw})


def test_le_sem_marcar_e_marca_depois() -> None:
    client = ImapMailClient(cfg(processed_folder="Faturas lidas"))
    client.open()
    assert client.unseen(1) == ["7"]
    assert client.fetch("7").startswith(b"From:")
    client.mark_processed("7")
    calls = FakeImap.last.calls
    assert ("FETCH", "7", "(BODY.PEEK[])") in calls  # PEEK: baixar não marca como lido
    assert ("STORE", "7", "+FLAGS", "(\\Seen)") in calls and ("MOVE", "7", '"Faturas lidas"') in calls
    client.close()
    client.close()  # fechar duas vezes não quebra


def test_erros_viram_mensagens_claras() -> None:
    with pytest.raises(MailboxUnavailable, match="senha do e-mail recusados"):
        ImapMailClient(cfg(password="errada")).open()
    with pytest.raises(MailboxUnavailable, match="pasta 'Outra' não encontrada"):
        ImapMailClient(cfg(folder="Outra", use_ssl=False)).open()
    with pytest.raises(MailboxUnavailable, match="não conectada"):
        ImapMailClient(cfg()).unseen(5)
    client = ImapMailClient(cfg(processed_folder="quebrada"))
    client.open()
    with pytest.raises(MailboxUnavailable, match="falha de rede"):
        client.mark_processed("7")


def test_razoes() -> None:
    assert "não encontrado" in imap._reason(socket.gaierror("x"))
    assert "a tempo" in imap._reason(TimeoutError())
    assert "SSL" in imap._reason(imap.ssl.SSLError("x"))
    assert imap._reason(RuntimeError("outra")) == "outra"
