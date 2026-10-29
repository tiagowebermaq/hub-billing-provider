from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date
from decimal import Decimal
from typing import Any

import httpx
from pydantic import BaseModel, Field

from hub_billing.collection.invoice import InvoiceParseError, ParsedInvoice, parse_text, pdf_text
from hub_billing.domain.models import Bill, ConsumerInfo, PropertyInfo, UtilityType

CONSUMPTION_UNITS = {UtilityType.ENERGY: "kWh", UtilityType.WATER: "m3", UtilityType.TELECOM: None}


class ProviderCredentials(BaseModel):
    login: str
    secret: str
    extra: dict[str, str] = Field(default_factory=dict)


class ProviderSession(BaseModel):
    provider: str
    data: dict[str, Any] = Field(default_factory=dict)


class ProviderAccount(BaseModel):
    account_number: str
    holder: str | None = None
    address: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class SessionExpired(RuntimeError):
    """A sessão do portal expirou e não pôde ser renovada: é preciso refazer o login assistido."""


class RawBill(BaseModel):
    """Conta como veio do provedor, antes da normalização."""

    provider: str
    account_number: str
    reference_month: str
    due_date: date
    amount: Decimal
    payload: dict[str, Any] = Field(default_factory=dict)


class ProviderAdapter(ABC):
    """Contrato único de todas as concessionárias. O núcleo do hub só conhece esta interface."""

    code: str
    utility_type: UtilityType
    account_labels: tuple[str, ...] = ()

    def __init__(self, distributor: str | None = None) -> None:
        self.distributor = distributor
        self.http: httpx.AsyncClient | None = None
        self.options: dict[str, str] = {}

    def use(self, http: httpx.AsyncClient, **options: str) -> ProviderAdapter:
        """Injeta o cliente HTTP e opções (ex.: chaves públicas do portal) usados na coleta automática."""
        self.http = http
        self.options = options
        return self

    def _client(self) -> httpx.AsyncClient:
        if self.http is None:
            raise RuntimeError(f"adapter {self.code} sem cliente HTTP: chame use(http) antes de coletar")
        return self.http

    async def resume(self, data: dict[str, Any]) -> ProviderSession:
        """Retoma a sessão salva pelo login assistido (tools/login_portal.py), renovando o token se preciso."""
        raise NotImplementedError(f"{self.code}: coleta automática ainda não disponível")

    @abstractmethod
    async def authenticate(self, credentials: ProviderCredentials) -> ProviderSession: ...

    @abstractmethod
    async def list_accounts(self, session: ProviderSession) -> list[ProviderAccount]: ...

    @abstractmethod
    async def fetch_bills(
        self, session: ProviderSession, account: ProviderAccount, reference: str | None = None
    ) -> list[RawBill]: ...

    @abstractmethod
    async def download_document(self, session: ProviderSession, bill: RawBill) -> bytes: ...

    def parse_invoice(self, pdf: bytes | None = None, text: str | None = None) -> ParsedInvoice:
        """Lê a fatura (PDF ou texto já extraído) com os rótulos desta concessionária."""
        if text is None:
            if pdf is None:
                raise InvoiceParseError("informe o PDF ou o texto da fatura")
            text = pdf_text(pdf)
        parsed = parse_text(text, self.account_labels)
        if parsed.barcode is not None and not parsed.barcode.matches(self.utility_type):
            parsed.warnings.append(
                f"código de barras do segmento '{parsed.barcode.segment_name}' não é de {self.utility_type.value}")
        return parsed

    def raw_from_invoice(self, parsed: ParsedInvoice, account_number: str | None = None,
                         reference_month: str | None = None, source: str = "upload") -> RawBill:
        """Converte a fatura lida em RawBill. Os campos informados pelo operador têm prioridade."""
        account = account_number or parsed.account_number
        reference = reference_month or parsed.reference_month
        missing = [n for n, v in (("número da conta", account), ("competência", reference),
                                  ("vencimento", parsed.due_date), ("valor", parsed.amount)) if v is None]
        if missing:
            raise InvoiceParseError("dados não encontrados na fatura: " + ", ".join(missing))
        assert account and reference and parsed.due_date and parsed.amount is not None
        payload: dict[str, Any] = {"source": source, "warnings": parsed.warnings}
        if parsed.barcode is not None:
            payload |= {"barcode": parsed.barcode.barcode, "digitable_line": parsed.barcode.digitable_line}
        if parsed.property:
            payload["property"] = parsed.property
        if parsed.consumer:
            payload["consumer"] = parsed.consumer
        return RawBill(provider=self.code, account_number=account, reference_month=reference,
                       due_date=parsed.due_date, amount=parsed.amount, payload=payload)

    def to_canonical(self, raw: RawBill) -> Bill:
        p = raw.payload
        return Bill(
            provider=self.code,
            distributor=self.distributor,
            utility_type=self.utility_type,
            account_number=raw.account_number,
            reference_month=raw.reference_month,
            due_date=raw.due_date,
            amount=raw.amount,
            issue_date=p.get("issue_date"),
            consumption=p.get("consumption"),
            consumption_unit=CONSUMPTION_UNITS[self.utility_type],
            barcode=p.get("barcode"),
            digitable_line=p.get("digitable_line"),
            pix_code=p.get("pix_code"),
            document_url=p.get("document_url"),
            property_info=PropertyInfo.model_validate(p["property"]) if p.get("property") else None,
            consumer=ConsumerInfo.model_validate(p["consumer"]) if p.get("consumer") else None,
        )
