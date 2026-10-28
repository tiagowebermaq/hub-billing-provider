"""Persistência em PostgreSQL (SQLite nos testes) com SQLAlchemy Core.

Guarda os vínculos conta -> imóvel/contrato, as contas normalizadas e os lotes enviados ao sistema de aluguéis.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Engine,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    insert,
    select,
    update,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import StaticPool

from hub_billing.domain.models import BatchStatus, Bill, BillingBatch, BillStatus

metadata = MetaData()

account_links = Table(
    "account_links", metadata,
    Column("provider", String(32), primary_key=True),
    Column("account_number", String(64), primary_key=True),
    Column("property_id", String(64), nullable=False),
    Column("contract_id", String(64)),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

bills = Table(
    "bills", metadata,
    Column("id", String(36), primary_key=True),
    Column("business_key", String(160), nullable=False, unique=True),
    Column("provider", String(32), nullable=False),
    Column("account_number", String(64), nullable=False),
    Column("reference_month", String(7), nullable=False, index=True),
    Column("property_id", String(64), index=True),
    Column("status", String(20), nullable=False, index=True),
    Column("batch_id", String(36), index=True),
    Column("data", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

batches = Table(
    "batches", metadata,
    Column("id", String(36), primary_key=True),
    Column("property_id", String(64), nullable=False),
    Column("contract_id", String(64)),
    Column("reference_month", String(7), nullable=False, index=True),
    Column("status", String(20), nullable=False, index=True),
    Column("correlation_id", String(64)),
    Column("data", JSON, nullable=False),
    Column("last_error", Text),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)


def _now() -> datetime:
    return datetime.now(UTC)


def make_engine(url: str) -> Engine:
    if url.startswith("sqlite") and ":memory:" in url:
        return create_engine(url, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    return create_engine(url, pool_pre_ping=True)


class Repository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    @classmethod
    def from_url(cls, url: str) -> Repository:
        return cls(make_engine(url))

    def create_schema(self) -> None:
        metadata.create_all(self.engine)

    # Vínculos conta -> imóvel -------------------------------------------------------------
    def link_account(self, provider: str, account_number: str, property_id: str, contract_id: str | None) -> int:
        """Cria/atualiza o vínculo e libera as contas da conta que estavam em revisão manual. Devolve quantas."""
        row = {"property_id": property_id, "contract_id": contract_id, "updated_at": _now()}
        key = (account_links.c.provider == provider) & (account_links.c.account_number == account_number)
        with self.engine.begin() as conn:
            if conn.execute(update(account_links).where(key).values(**row)).rowcount == 0:
                conn.execute(insert(account_links).values(provider=provider, account_number=account_number, **row))
            pending = conn.execute(select(bills.c.id, bills.c.data).where(
                (bills.c.provider == provider) & (bills.c.account_number == account_number)
                & (bills.c.status == BillStatus.MANUAL_REVIEW))).all()
            for bill_id, data in pending:
                data = {**data, "property_id": property_id, "contract_id": contract_id, "status": BillStatus.NORMALIZED}
                conn.execute(update(bills).where(bills.c.id == bill_id).values(
                    property_id=property_id, status=BillStatus.NORMALIZED, data=data))
        return len(pending)

    def find_link(self, provider: str, account_number: str) -> tuple[str, str | None] | None:
        with self.engine.connect() as conn:
            row = conn.execute(select(account_links.c.property_id, account_links.c.contract_id).where(
                (account_links.c.provider == provider) & (account_links.c.account_number == account_number))).first()
        return None if row is None else (row[0], row[1])

    def list_links(self) -> list[dict[str, Any]]:
        with self.engine.connect() as conn:
            rows = conn.execute(select(account_links).order_by(account_links.c.provider, account_links.c.account_number))
            return [dict(r._mapping) for r in rows]

    # Contas -------------------------------------------------------------------------------
    def save_bill(self, bill: Bill) -> bool:
        """Grava a conta. Sem imóvel vinculado ela fica em MANUAL_REVIEW. False se a chave de negócio já existe."""
        if bill.property_id is None:
            bill = bill.model_copy(update={"status": BillStatus.MANUAL_REVIEW})
        try:
            with self.engine.begin() as conn:
                conn.execute(insert(bills).values(
                    id=bill.id, business_key=bill.business_key, provider=bill.provider,
                    account_number=bill.account_number, reference_month=bill.reference_month,
                    property_id=bill.property_id, status=bill.status, data=bill.model_dump(mode="json"),
                    created_at=_now()))
        except IntegrityError:
            return False
        return True

    def list_bills(self, reference_month: str | None = None, status: BillStatus | None = None) -> list[Bill]:
        query = select(bills.c.data, bills.c.status).order_by(bills.c.created_at)
        if reference_month:
            query = query.where(bills.c.reference_month == reference_month)
        if status:
            query = query.where(bills.c.status == status)
        with self.engine.connect() as conn:
            return [Bill.model_validate({**r[0], "status": r[1]}) for r in conn.execute(query)]

    # Lotes --------------------------------------------------------------------------------
    def save_batch(self, batch: BillingBatch) -> None:
        """Grava o lote e marca as contas dele como GROUPED."""
        with self.engine.begin() as conn:
            conn.execute(insert(batches).values(
                id=batch.id, property_id=batch.property_id, contract_id=batch.contract_id,
                reference_month=batch.reference_month, status=batch.status, correlation_id=batch.correlation_id,
                data=batch.model_dump(mode="json"), updated_at=_now()))
            for bill in batch.bills:
                conn.execute(update(bills).where(bills.c.business_key == bill.business_key).values(
                    status=BillStatus.GROUPED, batch_id=batch.id, data=bill.model_dump(mode="json")))

    def get_batch(self, batch_id: str) -> BillingBatch | None:
        with self.engine.connect() as conn:
            row = conn.execute(select(batches.c.data, batches.c.status).where(batches.c.id == batch_id)).first()
        if row is None:
            return None
        return BillingBatch.model_validate({**row[0], "status": row[1]})

    def list_batches(self, reference_month: str | None = None) -> list[dict[str, Any]]:
        query = select(batches.c.id, batches.c.property_id, batches.c.contract_id, batches.c.reference_month,
                       batches.c.status, batches.c.last_error, batches.c.updated_at).order_by(batches.c.updated_at)
        if reference_month:
            query = query.where(batches.c.reference_month == reference_month)
        with self.engine.connect() as conn:
            return [dict(r._mapping) for r in conn.execute(query)]

    def set_batch_status(self, batch_id: str, status: BatchStatus, error: str | None = None) -> None:
        bill_status = {BatchStatus.SENT: BillStatus.SENT, BatchStatus.ACKED: BillStatus.ACKNOWLEDGED,
                       BatchStatus.FAILED: BillStatus.FAILED}.get(status)
        with self.engine.begin() as conn:
            conn.execute(update(batches).where(batches.c.id == batch_id).values(
                status=status, last_error=error, updated_at=_now()))
            if bill_status is not None:
                conn.execute(update(bills).where(bills.c.batch_id == batch_id).values(status=bill_status))
