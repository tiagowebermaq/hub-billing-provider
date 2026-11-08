"""Cliente IMAP da caixa de faturas (imaplib da biblioteca padrão, sem dependência nova)."""
from __future__ import annotations

import imaplib
import socket
import ssl
from typing import Protocol

from hub_billing.adapters.email_inbox.adapter import MailboxConfig


class MailboxUnavailable(RuntimeError):
    """Não deu para falar com a caixa de e-mail (rede, servidor ou senha). O poller tenta de novo com espera."""


class MailClient(Protocol):
    def open(self) -> None: ...
    def unseen(self, limit: int) -> list[str]: ...
    def fetch(self, uid: str) -> bytes: ...
    def mark_processed(self, uid: str) -> None: ...
    def close(self) -> None: ...


def _reason(exc: BaseException) -> str:
    text = str(exc)
    if isinstance(exc, imaplib.IMAP4.error) and ("AUTH" in text.upper() or "LOGIN" in text.upper()):
        return "usuário ou senha do e-mail recusados pelo servidor"
    if isinstance(exc, socket.gaierror):
        return "servidor de e-mail não encontrado: confira o endereço IMAP"
    if isinstance(exc, TimeoutError | socket.timeout):
        return "o servidor de e-mail não respondeu a tempo"
    if isinstance(exc, ssl.SSLError):
        return f"falha de SSL com o servidor de e-mail: {text}"
    if isinstance(exc, ConnectionError | OSError):
        return f"falha de rede com o servidor de e-mail: {exc.__class__.__name__}"
    return text or exc.__class__.__name__


class ImapMailClient:
    """Lê com BODY.PEEK (não marca como lido ao baixar): só marca depois que o e-mail entrou na fila."""

    def __init__(self, config: MailboxConfig, timeout: float = 30) -> None:
        self.config = config
        self.timeout = timeout
        self._conn: imaplib.IMAP4 | None = None

    def _call(self, *args: str) -> list[object]:
        conn = self._require()
        try:
            typ, data = conn.uid(*args)
        except (imaplib.IMAP4.error, OSError) as exc:
            raise MailboxUnavailable(_reason(exc)) from exc
        if typ != "OK":
            raise MailboxUnavailable(f"o servidor de e-mail recusou {args[0]}: {data!r}")
        return list(data)

    def _require(self) -> imaplib.IMAP4:
        if self._conn is None:
            raise MailboxUnavailable("caixa de e-mail não conectada")
        return self._conn

    def open(self) -> None:
        cfg = self.config
        try:
            if cfg.use_ssl:
                conn: imaplib.IMAP4 = imaplib.IMAP4_SSL(cfg.host, cfg.port, ssl_context=ssl.create_default_context(),
                                                        timeout=self.timeout)
            else:
                conn = imaplib.IMAP4(cfg.host, cfg.port, timeout=self.timeout)
            conn.login(cfg.username, cfg.password)
            typ, data = conn.select(f'"{cfg.folder}"')
        except (imaplib.IMAP4.error, OSError) as exc:
            raise MailboxUnavailable(_reason(exc)) from exc
        if typ != "OK":
            raise MailboxUnavailable(f"pasta '{cfg.folder}' não encontrada na caixa de e-mail")
        self._conn = conn

    def unseen(self, limit: int) -> list[str]:
        data = self._call("SEARCH", "UNSEEN")
        first = data[0] if data else b""
        uids = first.split() if isinstance(first, bytes) else []
        return [u.decode() for u in uids[:limit]]

    def fetch(self, uid: str) -> bytes:
        for item in self._call("FETCH", uid, "(BODY.PEEK[])"):
            if isinstance(item, tuple) and len(item) > 1 and isinstance(item[1], bytes):
                return item[1]
        raise MailboxUnavailable(f"e-mail {uid} veio vazio do servidor")

    def mark_processed(self, uid: str) -> None:
        self._call("STORE", uid, "+FLAGS", "(\\Seen)")
        target = self.config.processed_folder
        if target and "MOVE" in self._require().capabilities:
            self._call("MOVE", uid, f'"{target}"')

    def close(self) -> None:
        conn, self._conn = self._conn, None
        if conn is None:
            return
        try:
            conn.logout()
        except (imaplib.IMAP4.error, OSError):
            pass  # conexão já caiu: nada a fechar
