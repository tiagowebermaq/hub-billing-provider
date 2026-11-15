"""Cliente POP3 da caixa de faturas, para provedores sem IMAP (poplib da biblioteca padrão).

POP3 não marca mensagem como lida: o hub guarda no banco o identificador (UIDL) de cada uma que já leu. Na primeira
conexão ele registra o que já estava na caixa; essas mensagens são lidas (para trazer faturas antigas), mas o hub
nunca as apaga. Com "apagar depois de N dias", só as mensagens que chegaram depois de ligar o hub são apagadas, N dias
depois de lidas. Sem esse valor (0), nada é apagado do servidor.
"""
from __future__ import annotations

import poplib
import ssl
from datetime import UTC, datetime, timedelta
from typing import Protocol

from hub_billing.adapters.email_inbox.adapter import MailboxConfig
from hub_billing.adapters.email_inbox.imap import MailboxUnavailable, reason


class Pop3Store(Protocol):
    def pop3_state(self, account: str) -> dict[str, tuple[datetime | None, bool]] | None: ...
    def pop3_baseline(self, account: str, uids: list[str]) -> None: ...
    def pop3_mark(self, account: str, uid: str) -> None: ...
    def pop3_forget(self, account: str, uids: list[str]) -> None: ...


class Pop3MailClient:
    def __init__(self, config: MailboxConfig, store: Pop3Store, timeout: float = 30,
                 now: datetime | None = None) -> None:
        self.config = config
        self.store = store
        self.timeout = timeout
        self.now = now
        self.account = f"{config.username}@{config.host}"
        self._conn: poplib.POP3 | None = None
        self._numbers: dict[str, bytes] = {}
        self._deleted: list[str] = []

    def _require(self) -> poplib.POP3:
        if self._conn is None:
            raise MailboxUnavailable("caixa de e-mail não conectada")
        return self._conn

    def open(self) -> None:
        cfg = self.config
        try:
            if cfg.use_ssl:
                conn: poplib.POP3 = poplib.POP3_SSL(cfg.host, cfg.port, timeout=self.timeout,
                                                    context=ssl.create_default_context())
            else:
                conn = poplib.POP3(cfg.host, cfg.port, timeout=self.timeout)
            conn.user(cfg.username)
            conn.pass_(cfg.password)
        except (poplib.error_proto, OSError) as exc:
            raise MailboxUnavailable(reason(exc)) from exc
        self._conn = conn

    def unseen(self, limit: int) -> list[str]:
        conn = self._require()
        try:
            _, lines, _ = conn.uidl()
        except (poplib.error_proto, OSError) as exc:
            raise MailboxUnavailable(reason(exc)) from exc
        self._numbers = {}
        for line in lines:
            parts = line.split()
            if len(parts) == 2:
                self._numbers[parts[1].decode(errors="replace")] = parts[0]
        state = self.store.pop3_state(self.account)
        if state is None:
            self.store.pop3_baseline(self.account, list(self._numbers))
            state = {uid: (None, False) for uid in self._numbers}
        self._delete_old(state)
        pending = [uid for uid in self._numbers if uid not in state or state[uid][0] is None]
        return pending[:limit]

    def _delete_old(self, state: dict[str, tuple[datetime | None, bool]]) -> None:
        days = self.config.delete_after_days
        if days <= 0:
            return
        limit = (self.now or datetime.now(UTC)) - timedelta(days=days)
        for uid, number in self._numbers.items():
            processed_at, deletable = state.get(uid, (None, False))
            if deletable and processed_at is not None and processed_at < limit:
                try:
                    self._require().dele(int(number))
                except (poplib.error_proto, OSError) as exc:
                    raise MailboxUnavailable(reason(exc)) from exc
                self._deleted.append(uid)

    def fetch(self, uid: str) -> bytes:
        number = self._numbers.get(uid)
        if number is None:
            raise MailboxUnavailable(f"e-mail {uid} não está mais na caixa")
        try:
            _, lines, _ = self._require().retr(int(number))
        except (poplib.error_proto, OSError) as exc:
            raise MailboxUnavailable(reason(exc)) from exc
        return b"\r\n".join(lines) + b"\r\n"

    def mark_processed(self, uid: str) -> None:
        self.store.pop3_mark(self.account, uid)

    def close(self) -> None:
        """QUIT confirma as exclusões; se a conexão caiu antes, o servidor desfaz e elas são tentadas de novo."""
        conn, self._conn = self._conn, None
        if conn is None:
            return
        try:
            conn.quit()
        except (poplib.error_proto, OSError):
            return
        self.store.pop3_forget(self.account, self._deleted)
        self._deleted = []
