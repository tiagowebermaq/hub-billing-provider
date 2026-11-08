"""E-mails sintéticos e um cliente IMAP falso para os testes da caixa de faturas."""
from __future__ import annotations

from email.message import EmailMessage

from hub_billing.adapters.email_inbox.adapter import MailboxConfig
from hub_billing.adapters.email_inbox.imap import MailboxUnavailable


def make_email(sender: str = "fatura@rge-rs.com.br", subject: str = "Sua conta RGE chegou", body: str = "Segue a fatura.",
               pdfs: list[tuple[str, bytes]] | None = None, message_id: str = "<m1@rge>", html: str | None = None) -> bytes:
    msg = EmailMessage()
    msg["From"] = f"RGE <{sender}>"
    msg["To"] = "41u6u31@heimber.com.br"
    msg["Subject"] = subject
    msg["Message-ID"] = message_id
    msg["Date"] = "Sat, 07 Nov 2026 10:00:00 -0300"
    msg.set_content(body)
    if html is not None:
        msg.add_alternative(html, subtype="html")
    for name, content in pdfs or []:
        msg.add_attachment(content, maintype="application", subtype="pdf", filename=name)
    return msg.as_bytes()


def forward(original: bytes, by: str = "weber@gmail.com", message_id: str = "<fwd1@gmail>") -> bytes:
    msg = EmailMessage()
    msg["From"] = by
    msg["To"] = "41u6u31@heimber.com.br"
    msg["Subject"] = "Fwd: conta do mês"
    msg["Message-ID"] = message_id
    msg.set_content("Encaminhando.")
    msg.add_attachment(original, maintype="message", subtype="rfc822")
    return msg.as_bytes()


class FakeMailClient:
    """Caixa IMAP em memória. `fail_on` faz um passo levantar MailboxUnavailable."""

    instances: list[FakeMailClient] = []

    def __init__(self, messages: dict[str, bytes], fail_on: str | None = None) -> None:
        self.messages = messages
        self.seen: set[str] = set()
        self.fail_on = fail_on
        self.opened = self.closed = 0

    def __call__(self, config: MailboxConfig) -> FakeMailClient:  # usado como fábrica
        self.config = config
        return self

    def _maybe_fail(self, step: str) -> None:
        if self.fail_on == step:
            raise MailboxUnavailable(f"falha simulada em {step}")

    def open(self) -> None:
        self._maybe_fail("open")
        self.opened += 1

    def unseen(self, limit: int) -> list[str]:
        self._maybe_fail("unseen")
        return [u for u in self.messages if u not in self.seen][:limit]

    def fetch(self, uid: str) -> bytes:
        self._maybe_fail("fetch")
        return self.messages[uid]

    def mark_processed(self, uid: str) -> None:
        self._maybe_fail("mark")
        self.seen.add(uid)

    def close(self) -> None:
        self.closed += 1
