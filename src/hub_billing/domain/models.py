from __future__ import annotations

import re
import uuid
from datetime import date
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator

_YM = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


class UtilityType(StrEnum):
    ENERGY = "ENERGY"
    WATER = "WATER"
    TELECOM = "TELECOM"


class BillStatus(StrEnum):
    COLLECTED = "COLLECTED"
    NORMALIZED = "NORMALIZED"
    GROUPED = "GROUPED"
    SENT = "SENT"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    DUPLICATE = "DUPLICATE"
    FAILED = "FAILED"
    MANUAL_REVIEW = "MANUAL_REVIEW"


class BatchStatus(StrEnum):
    OPEN = "OPEN"
    READY = "READY"
    SENT = "SENT"
    ACKED = "ACKED"
    FAILED = "FAILED"


class PropertyInfo(BaseModel):
    """Cadastro do imóvel na concessionária (unidade consumidora / matrícula)."""

    address: str | None = None
    zip_code: str | None = None
    city: str | None = None
    state: str | None = None
    category: str | None = None
    connection_type: str | None = None
    meter: str | None = None
    status: str | None = None


class ConsumerInfo(BaseModel):
    """Titular da conta na concessionária."""

    name: str | None = None
    document: str | None = None
    holder_since: date | None = None
    email: str | None = None


def fill_gaps[T: BaseModel](primary: T | None, fallback: T | None) -> T | None:
    """Junta dois cadastros: o primeiro vale, o segundo só completa os campos vazios."""
    if primary is None or fallback is None:
        return primary or fallback
    merged = {k: v if v is not None else getattr(fallback, k) for k, v in primary}
    return type(primary).model_validate(merged)


class Bill(BaseModel):
    """Conta no modelo canônico, independente do provedor."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    provider: str
    distributor: str | None = None
    utility_type: UtilityType
    account_number: str
    property_id: str | None = None
    contract_id: str | None = None
    reference_month: str
    issue_date: date | None = None
    due_date: date
    amount: Decimal = Field(ge=0)
    consumption: Decimal | None = None
    consumption_unit: str | None = None
    barcode: str | None = None
    digitable_line: str | None = None
    pix_code: str | None = None
    document_url: str | None = None
    property_info: PropertyInfo | None = None
    consumer: ConsumerInfo | None = None
    status: BillStatus = BillStatus.NORMALIZED
    correlation_id: str | None = None

    @field_validator("reference_month")
    @classmethod
    def _check_ym(cls, v: str) -> str:
        if not _YM.match(v):
            raise ValueError("reference_month deve estar no formato AAAA-MM")
        return v

    @property
    def business_key(self) -> str:
        """Chave de idempotência de negócio: provedor + conta + competência."""
        return f"{self.provider}:{self.account_number}:{self.reference_month}"


class BillingBatch(BaseModel):
    """Lote de contas de um imóvel/contrato em uma competência, entregue ao sistema de aluguéis."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    property_id: str
    contract_id: str | None
    reference_month: str
    bills: list[Bill]
    status: BatchStatus = BatchStatus.READY
    correlation_id: str | None = None

    @property
    def total(self) -> Decimal:
        return sum((b.amount for b in self.bills), Decimal("0"))
