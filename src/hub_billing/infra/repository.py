"""Persistência em PostgreSQL (SQLite nos testes) com SQLAlchemy Core.

Guarda os vínculos conta -> imóvel/contrato, as contas normalizadas e os lotes enviados ao sistema de aluguéis.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, NamedTuple

from cryptography.fernet import InvalidToken
from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Engine,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    insert,
    inspect,
    select,
    update,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import StaticPool

from hub_billing.core.security import SecretBox
from hub_billing.domain.models import (
    BatchStatus,
    Bill,
    BillingBatch,
    BillStatus,
    ConsumerInfo,
    PropertyInfo,
    fill_gaps,
)

metadata = MetaData()

account_links = Table(
    "account_links", metadata,
    Column("provider", String(32), primary_key=True),
    Column("account_number", String(64), primary_key=True),
    Column("property_id", String(64), nullable=False),
    Column("contract_id", String(64)),
    Column("registry", JSON),
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

provider_sessions = Table(
    "provider_sessions", metadata,
    Column("provider", String(32), primary_key=True),
    Column("distributor", String(32), primary_key=True),
    Column("data_enc", Text, nullable=False),
    Column("expires_at", DateTime(timezone=True)),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

SCHEMA_LOCK_ID = 7_041_983  # número qualquer, fixo: identifica a trava de criação das tabelas do hub

collection_runs = Table(
    "collection_runs", metadata,
    Column("id", String(64), primary_key=True),  # message_id do collect.request
    Column("provider", String(32), nullable=False),
    Column("distributor", String(32), nullable=False, default=""),
    Column("status", String(16), nullable=False),  # ok | login | erro
    Column("accounts", Integer, nullable=False, default=0),
    Column("bills", Integer, nullable=False, default=0),
    Column("message", Text),
    Column("finished_at", DateTime(timezone=True), nullable=False),
)

users = Table(
    "users", metadata,
    Column("id", String(36), primary_key=True),
    Column("email", String(254), nullable=False, unique=True),
    Column("password_hash", Text, nullable=False),
    Column("name", String(120), nullable=False, default=""),
    Column("is_admin", Boolean, nullable=False, default=False),
    Column("active", Boolean, nullable=False, default=True),
    Column("must_change_password", Boolean, nullable=False, default=False),
    Column("roles", JSON, nullable=False),
    Column("extra_permissions", JSON, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)


def _now() -> datetime:
    return datetime.now(UTC)


class AccountLink(NamedTuple):
    """Vínculo da conta com o imóvel/contrato e o cadastro do imóvel e do titular feito no hub."""

    property_id: str
    contract_id: str | None
    property_info: PropertyInfo | None = None
    consumer: ConsumerInfo | None = None

    def apply(self, bill: Bill) -> Bill:
        """Vincula a conta ao imóvel. O que veio na fatura vale; o cadastro do hub completa o que faltar."""
        return bill.model_copy(update={
            "property_id": self.property_id, "contract_id": self.contract_id,
            "property_info": fill_gaps(bill.property_info, self.property_info),
            "consumer": fill_gaps(bill.consumer, self.consumer)})


def _registry(property_info: PropertyInfo | None, consumer: ConsumerInfo | None) -> dict[str, Any] | None:
    data = {k: v.model_dump(mode="json", exclude_none=True) for k, v in
            (("property", property_info), ("consumer", consumer)) if v is not None}
    return data or None


def make_engine(url: str) -> Engine:
    if url.startswith("sqlite") and ":memory:" in url:
        return create_engine(url, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    return create_engine(url, pool_pre_ping=True)


class Repository:
    def __init__(self, engine: Engine, box: SecretBox | None = None) -> None:
        self.engine = engine
        self.box = box or SecretBox("")

    @classmethod
    def from_url(cls, url: str, encryption_key: str = "") -> Repository:
        return cls(make_engine(url), SecretBox(encryption_key))

    def create_schema(self) -> None:
        """Cria as tabelas que faltam. A API e os 5 workers sobem juntos e todos chamam isto: no PostgreSQL, uma
        trava (advisory lock) faz um de cada vez, senão dois tentam criar a mesma tabela e um deles cai."""
        with self.engine.begin() as conn:
            if conn.dialect.name == "postgresql":
                conn.exec_driver_sql(f"SELECT pg_advisory_xact_lock({SCHEMA_LOCK_ID})")
            metadata.create_all(conn)
            # Bancos criados antes do cadastro do imóvel/titular não têm a coluna registry.
            columns = {c["name"] for c in inspect(conn).get_columns("account_links")}
            if "registry" not in columns:
                conn.exec_driver_sql("ALTER TABLE account_links ADD COLUMN registry JSON")

    # Vínculos conta -> imóvel -------------------------------------------------------------
    def link_account(self, provider: str, account_number: str, property_id: str, contract_id: str | None,
                     property_info: PropertyInfo | None = None, consumer: ConsumerInfo | None = None) -> int:
        """Cria/atualiza o vínculo e libera as contas da conta que estavam em revisão manual. Devolve quantas."""
        link = AccountLink(property_id, contract_id, property_info, consumer)
        row = {"property_id": property_id, "contract_id": contract_id,
               "registry": _registry(property_info, consumer), "updated_at": _now()}
        key = (account_links.c.provider == provider) & (account_links.c.account_number == account_number)
        with self.engine.begin() as conn:
            if conn.execute(update(account_links).where(key).values(**row)).rowcount == 0:
                conn.execute(insert(account_links).values(provider=provider, account_number=account_number, **row))
            pending = conn.execute(select(bills.c.id, bills.c.data).where(
                (bills.c.provider == provider) & (bills.c.account_number == account_number)
                & (bills.c.status == BillStatus.MANUAL_REVIEW))).all()
            for bill_id, data in pending:
                bill = link.apply(Bill.model_validate(data)).model_copy(update={"status": BillStatus.NORMALIZED})
                conn.execute(update(bills).where(bills.c.id == bill_id).values(
                    property_id=property_id, status=BillStatus.NORMALIZED, data=bill.model_dump(mode="json")))
        return len(pending)

    def find_link(self, provider: str, account_number: str) -> AccountLink | None:
        with self.engine.connect() as conn:
            row = conn.execute(select(account_links.c.property_id, account_links.c.contract_id,
                                      account_links.c.registry).where(
                (account_links.c.provider == provider) & (account_links.c.account_number == account_number))).first()
        if row is None:
            return None
        registry = row[2] or {}
        return AccountLink(row[0], row[1],
                           PropertyInfo.model_validate(registry["property"]) if registry.get("property") else None,
                           ConsumerInfo.model_validate(registry["consumer"]) if registry.get("consumer") else None)

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

    # Sessões dos portais (tokens obtidos no login assistido), sempre cifradas ------------------
    def save_session(self, provider: str, distributor: str | None, data: dict[str, Any],
                     expires_at: datetime | None) -> None:
        enc = self.box.encrypt(json.dumps(data))
        key = (provider_sessions.c.provider == provider) & (provider_sessions.c.distributor == (distributor or ""))
        row = {"data_enc": enc, "expires_at": expires_at, "updated_at": _now()}
        with self.engine.begin() as conn:
            if conn.execute(update(provider_sessions).where(key).values(**row)).rowcount == 0:
                conn.execute(insert(provider_sessions).values(provider=provider, distributor=distributor or "", **row))

    def load_session(self, provider: str, distributor: str | None) -> tuple[dict[str, Any], datetime | None] | None:
        with self.engine.connect() as conn:
            row = conn.execute(select(provider_sessions.c.data_enc, provider_sessions.c.expires_at).where(
                (provider_sessions.c.provider == provider)
                & (provider_sessions.c.distributor == (distributor or "")))).first()
        if row is None:
            return None
        expires = row[1].replace(tzinfo=UTC) if row[1] is not None and row[1].tzinfo is None else row[1]
        try:
            return json.loads(self.box.decrypt(row[0])), expires
        except InvalidToken:  # HUB_ENCRYPTION_KEY trocada ou diferente entre API e workers
            return None

    def only_session(self, provider: str) -> str | None:
        """Distribuidora da única sessão salva do portal, para coletar sem informar a distribuidora."""
        with self.engine.connect() as conn:
            rows = conn.execute(select(provider_sessions.c.distributor).where(
                provider_sessions.c.provider == provider)).all()
        return rows[0][0] if len(rows) == 1 else None

    # Histórico das coletas, para o painel mostrar o que aconteceu em cada uma ------------------
    def record_run(self, run_id: str, provider: str, distributor: str | None, status: str, accounts: int = 0,
                   bills: int = 0, message: str | None = None) -> None:
        row = {"provider": provider, "distributor": distributor or "", "status": status, "accounts": accounts,
               "bills": bills, "message": message, "finished_at": _now()}
        with self.engine.begin() as conn:
            if conn.execute(update(collection_runs).where(collection_runs.c.id == run_id).values(**row)).rowcount == 0:
                conn.execute(insert(collection_runs).values(id=run_id, **row))

    def list_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        query = select(collection_runs).order_by(collection_runs.c.finished_at.desc()).limit(limit)
        with self.engine.connect() as conn:
            return [dict(r._mapping) for r in conn.execute(query)]

    def list_sessions(self) -> list[dict[str, Any]]:
        """Status das sessões, sem os tokens."""
        query = select(provider_sessions.c.provider, provider_sessions.c.distributor, provider_sessions.c.expires_at,
                       provider_sessions.c.updated_at).order_by(provider_sessions.c.provider)
        with self.engine.connect() as conn:
            return [dict(r._mapping) for r in conn.execute(query)]
