"""Adapter de e-mail: lê as faturas (CPFL/RGE, Aegea/Corsan, Vivo) que chegam na caixa do hub.

A caixa recebe o e-mail da concessionária direto ou encaminhado por alguém. Para cada PDF anexo (ou, sem PDF, o
corpo com a linha digitável) o adapter descobre a concessionária pelo conteúdo da fatura, pelo assunto e pelo
remetente, e lê a conta com o adapter da própria concessionária. O resultado é o mesmo RawBill da coleta pelos
portais, então segue pelo mesmo caminho de filas (collect.result -> normalizador -> agrupador).
"""
from __future__ import annotations

import base64
import hashlib
import html
import quopri
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from email import policy
from email.message import EmailMessage, Message
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime
from typing import NamedTuple

from pydantic import BaseModel, Field

from hub_billing.adapters.base import RawBill
from hub_billing.adapters.registry import get_adapter
from hub_billing.collection.invoice import InvoiceParseError, pdf_text, strip_accents
from hub_billing.domain.barcode import find_in_text

DEFAULT_ADDRESS = "41u6u31@heimber.com.br"


class MailboxConfig(BaseModel):
    """Acesso IMAP à caixa que recebe as faturas. Vem do painel (aba E-mail) ou, sem cadastro lá, do .env."""

    host: str = ""
    port: int = 993
    username: str = DEFAULT_ADDRESS
    password: str = ""
    use_ssl: bool = True
    folder: str = "INBOX"
    processed_folder: str = ""  # vazio: só marca como lido
    poll_seconds: int = 300
    pdf_passwords: list[str] = Field(default_factory=list)

    @property
    def configured(self) -> bool:
        return bool(self.host and self.username and self.password)

    def public(self) -> dict[str, object]:
        """Para o painel: sem a senha nem as senhas dos PDFs."""
        data = self.model_dump(exclude={"password", "pdf_passwords"})
        return data | {"has_password": bool(self.password), "pdf_passwords": len(self.pdf_passwords)}


@dataclass
class Attachment:
    filename: str
    content_type: str
    content: bytes

    @property
    def is_pdf(self) -> bool:
        return self.content_type == "application/pdf" or self.filename.lower().endswith(".pdf") \
            or self.content[:5] == b"%PDF-"


@dataclass
class InboundEmail:
    id: str  # chave estável derivada do Message-ID, usada na tabela e nas filas
    message_id: str
    sender: str
    subject: str
    received_at: datetime | None
    body: str
    forwarded_from: list[str] = field(default_factory=list)
    attachments: list[Attachment] = field(default_factory=list)

    @property
    def pdfs(self) -> list[Attachment]:
        return [a for a in self.attachments if a.is_pdf]


def email_key(message_id: str) -> str:
    return hashlib.sha256(message_id.encode()).hexdigest()[:40]


_TAGS = re.compile(r"<(script|style)\b.*?</\1>|<[^>]+>", re.IGNORECASE | re.DOTALL)


def _html_text(value: str) -> str:
    return html.unescape(_TAGS.sub(" ", value.replace("<br", "\n<br").replace("</p>", "</p>\n")))


def _encoded_message(part: Message) -> EmailMessage | None:
    """E-mail encaminhado como anexo com base64/quoted-printable (fora do padrão, mas acontece): decodifica."""
    cte = str(part.get("Content-Transfer-Encoding", "")).lower()
    children = part.get_payload()
    if cte not in ("base64", "quoted-printable") or not isinstance(children, list) or not children \
            or not isinstance(children[0], Message):
        return None
    text = str(children[0].get_payload())
    data = base64.b64decode(text, validate=False) if cte == "base64" else quopri.decodestring(text.encode())
    inner = BytesParser(policy=policy.default).parsebytes(data)
    return inner if isinstance(inner, EmailMessage) else None


def _collect(part: Message, body: list[str], forwarded: list[str], attachments: list[Attachment],
             top: bool = True) -> None:
    if not top and part.get("From"):  # cabeçalho de um e-mail encaminhado como anexo
        forwarded += [a for _, a in getaddresses([str(part.get("From", ""))]) if a]
    ctype = part.get_content_type()
    if ctype == "message/rfc822" and (inner := _encoded_message(part)) is not None:
        _collect(inner, body, forwarded, attachments, top=False)
        return
    if part.is_multipart():
        for child in part.get_payload():
            if isinstance(child, Message):
                _collect(child, body, forwarded, attachments, top=False)
        return
    payload = part.get_payload(decode=True)
    if not isinstance(payload, bytes):
        return
    filename = part.get_filename() or ""
    if filename or part.get_content_disposition() == "attachment" or ctype == "application/pdf":
        attachments.append(Attachment(filename or "anexo.pdf", ctype, payload))
    elif ctype in ("text/plain", "text/html"):
        text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        body.append(_html_text(text) if ctype == "text/html" else text)


def parse_message(raw: bytes) -> InboundEmail:
    """Lê o e-mail cru (RFC 822), incluindo e-mails encaminhados como anexo."""
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    message_id = str(msg.get("Message-ID", "")).strip() or "sha256:" + hashlib.sha256(raw).hexdigest()
    try:
        received_at = parsedate_to_datetime(str(msg["Date"])) if msg.get("Date") else None
    except (TypeError, ValueError):
        received_at = None
    sender = (getaddresses([str(msg.get("From", ""))]) or [("", "")])[0][1].lower()
    body: list[str] = []
    forwarded: list[str] = []
    attachments: list[Attachment] = []
    _collect(msg, body, forwarded, attachments)
    # Corpo do e-mail encaminhado no texto ("De: fatura@cpfl.com.br")
    forwarded += re.findall(r"^(?:>\s*)?(?:De|From):.*?([\w.+-]+@[\w-]+(?:\.[\w-]+)+)", "\n".join(body), re.MULTILINE)
    return InboundEmail(id=email_key(message_id), message_id=message_id, sender=sender,
                        subject=str(msg.get("Subject", "")), received_at=received_at, body="\n".join(body),
                        forwarded_from=list(dict.fromkeys(a.lower() for a in forwarded)), attachments=attachments)


class ProviderMatch(NamedTuple):
    provider: str
    distributor: str | None


@dataclass(frozen=True)
class _Rule:
    provider: str
    distributor: str | None
    domains: tuple[str, ...]
    words: tuple[str, ...]


# Pontos somam por concessionária; a distribuidora é a regra da concessionária vencedora com mais pontos.
RULES = (
    _Rule("cpfl", "rge", ("rge-rs.com.br", "rgesul.com.br"), ("RGE SUL", "RGE")),
    _Rule("cpfl", "piratininga", (), ("CPFL PIRATININGA",)),
    _Rule("cpfl", "santa-cruz", (), ("CPFL SANTA CRUZ",)),
    _Rule("cpfl", "paulista", (), ("CPFL PAULISTA",)),
    _Rule("cpfl", None, ("cpfl.com.br", "cpflenergia.com.br"), ("CPFL",)),
    _Rule("aegea", "corsan", ("corsan.com.br", "corsan.rs.gov.br"), ("CORSAN",)),
    _Rule("aegea", None, ("aegea.com.br",), ("AEGEA",)),
    _Rule("vivo", None, ("vivo.com.br", "telefonica.com.br", "telefonica.com"), ("VIVO", "TELEFONICA BRASIL")),
)
_SEGMENT_PROVIDER = {"3": ProviderMatch("cpfl", None), "2": ProviderMatch("aegea", None), "4": ProviderMatch("vivo", None)}


def _domain_hit(address: str, domains: Iterable[str]) -> bool:
    host = address.rsplit("@", 1)[-1]
    return any(host == d or host.endswith("." + d) for d in domains)


def identify(email: InboundEmail, document_text: str = "", filename: str = "") -> ProviderMatch | None:
    """Concessionária da fatura. O texto da fatura vale mais que o assunto, que vale mais que o remetente
    (o e-mail pode ter sido encaminhado por uma pessoa). Sem pista nenhuma, usa o segmento do código de barras."""
    doc = strip_accents(f"{document_text}\n{filename}").upper()
    subject = strip_accents(email.subject).upper()
    senders = (email.sender, *email.forwarded_from)
    by_provider: dict[str, int] = {}
    by_distributor: dict[tuple[str, str], int] = {}
    for rule in RULES:
        score = 3 * any(_domain_hit(a, rule.domains) for a in senders)
        for word in rule.words:
            pattern = r"\b" + re.escape(word) + r"\b"
            score += 4 * min(len(re.findall(pattern, doc)), 5) + 2 * bool(re.search(pattern, subject))
        if score:
            by_provider[rule.provider] = by_provider.get(rule.provider, 0) + score
            if rule.distributor:
                key = (rule.provider, rule.distributor)
                by_distributor[key] = by_distributor.get(key, 0) + score
    if by_provider:
        best = max(by_provider.values())
        winners = [p for p, s in by_provider.items() if s == best]
        if len(winners) == 1:
            options = {d: s for (p, d), s in by_distributor.items() if p == winners[0]}
            return ProviderMatch(winners[0], max(options, key=options.__getitem__) if options else None)
    barcode = find_in_text(strip_accents(document_text)) if document_text else None
    return _SEGMENT_PROVIDER.get(barcode.barcode[1]) if barcode else None


@dataclass
class EmailBill:
    raw: RawBill
    distributor: str | None
    document: bytes | None
    source: str  # nome do anexo ou "corpo do e-mail"


@dataclass
class EmailExtraction:
    bills: list[EmailBill] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def providers(self) -> list[str]:
        return sorted({b.raw.provider for b in self.bills})


class EmailAdapter:
    """Adapter de entrada por e-mail: transforma um InboundEmail em contas (RawBill) das concessionárias."""

    code = "email"

    def __init__(self, pdf_passwords: Iterable[str] = ()) -> None:
        self.pdf_passwords = tuple(p for p in pdf_passwords if p)

    def extract(self, email: InboundEmail) -> EmailExtraction:
        out = EmailExtraction()
        documents: list[tuple[str, str, bytes | None]] = []
        for att in email.pdfs:
            try:
                documents.append((att.filename, pdf_text(att.content, self.pdf_passwords), att.content))
            except InvoiceParseError as exc:
                out.problems.append(f"{att.filename}: {exc}")
        if not email.pdfs and find_in_text(strip_accents(email.body)) is not None:
            documents.append(("corpo do e-mail", email.body, None))
        for source, text, content in documents:
            match = identify(email, text, source)
            if match is None:
                out.problems.append(f"{source}: concessionária não identificada (CPFL/RGE, Aegea/Corsan ou Vivo)")
                continue
            adapter = get_adapter(match.provider, match.distributor)
            try:
                parsed = adapter.parse_invoice(text=text)
                raw = adapter.raw_from_invoice(parsed, source="email")
            except InvoiceParseError as exc:
                out.problems.append(f"{source}: {exc}")
                continue
            raw.payload["email"] = {"message_id": email.message_id, "from": email.sender, "subject": email.subject,
                                    "attachment": source}
            out.bills.append(EmailBill(raw, match.distributor, content, source))
        return out
