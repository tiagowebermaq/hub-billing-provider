from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from hub_billing.domain.models import Bill, UtilityType


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

    def __init__(self, distributor: str | None = None) -> None:
        self.distributor = distributor

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
            consumption_unit="kWh" if self.utility_type is UtilityType.ENERGY else "m3",
            barcode=p.get("barcode"),
            digitable_line=p.get("digitable_line"),
            pix_code=p.get("pix_code"),
            document_url=p.get("document_url"),
        )
