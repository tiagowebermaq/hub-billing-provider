from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text

from hub_billing.domain.grouping import group_bills
from hub_billing.domain.models import BatchStatus, Bill, BillStatus, ConsumerInfo, PropertyInfo, UtilityType
from hub_billing.infra.repository import Repository


@pytest.fixture
def repo() -> Repository:
    r = Repository.from_url("sqlite+pysqlite:///:memory:")
    r.create_schema()
    return r


def bill(account: str, prop: str | None = "imovel-1", provider: str = "cpfl", amount: str = "100.00") -> Bill:
    return Bill(provider=provider, utility_type=UtilityType.ENERGY, account_number=account, property_id=prop,
                reference_month="2026-10", due_date=date(2026, 11, 20), amount=Decimal(amount))


def test_vinculo_de_conta_cria_e_atualiza(repo: Repository) -> None:
    assert repo.find_link("cpfl", "uc1") is None
    repo.link_account("cpfl", "uc1", "imovel-1", None)
    repo.link_account("cpfl", "uc1", "imovel-2", "ctr-2")
    assert repo.find_link("cpfl", "uc1")[:2] == ("imovel-2", "ctr-2")
    assert [(r["account_number"], r["property_id"]) for r in repo.list_links()] == [("uc1", "imovel-2")]


def test_conta_duplicada_e_revisao_manual(repo: Repository) -> None:
    assert repo.save_bill(bill("uc1"))
    assert not repo.save_bill(bill("uc1"))
    assert repo.save_bill(bill("uc9", prop=None))
    assert [b.account_number for b in repo.list_bills(status=BillStatus.MANUAL_REVIEW)] == ["uc9"]
    assert repo.link_account("cpfl", "uc9", "imovel-9", "ctr-9") == 1
    released = repo.list_bills("2026-10", BillStatus.NORMALIZED)
    assert sorted((b.account_number, b.property_id) for b in released) == [("uc1", "imovel-1"), ("uc9", "imovel-9")]
    assert repo.list_bills("2026-09") == []


def test_lote_gravado_e_status_propagado(repo: Repository) -> None:
    for acc in ("uc1", "uc2"):
        repo.save_bill(bill(acc))
    [batch] = group_bills(repo.list_bills(status=BillStatus.NORMALIZED), "cid")
    repo.save_batch(batch)
    assert repo.list_bills(status=BillStatus.NORMALIZED) == []
    loaded = repo.get_batch(batch.id)
    assert loaded is not None and loaded.total == Decimal("200.00") and loaded.correlation_id == "cid"
    repo.set_batch_status(batch.id, BatchStatus.FAILED, "HTTP 500")
    assert repo.list_batches("2026-10")[0]["last_error"] == "HTTP 500"
    assert {b.status for b in repo.list_bills()} == {BillStatus.FAILED}
    repo.set_batch_status(batch.id, BatchStatus.ACKED)
    assert {b.status for b in repo.list_bills()} == {BillStatus.ACKNOWLEDGED}
    repo.set_batch_status(batch.id, BatchStatus.READY)
    assert repo.get_batch(batch.id).status is BatchStatus.READY  # type: ignore[union-attr]
    assert repo.get_batch("nao-existe") is None
    assert repo.list_batches("2026-01") == []


def test_engine_postgres_nao_conecta_na_criacao() -> None:
    r = Repository.from_url("postgresql+psycopg://hub:hub@localhost:1/hub")
    assert r.engine.dialect.name == "postgresql"


def test_cadastro_do_imovel_e_titular_no_vinculo(repo: Repository) -> None:
    repo.save_bill(bill("uc9", prop=None).model_copy(update={"consumer": ConsumerInfo(name="Ana")}))
    prop, consumer = PropertyInfo(address="Rua A, 10", city="Canoas"), ConsumerInfo(name="Outro", document="123")
    assert repo.link_account("cpfl", "uc9", "imovel-9", None, prop, consumer) == 1
    link = repo.find_link("cpfl", "uc9")
    assert link is not None and (link.property_info, link.consumer) == (prop, consumer)
    assert repo.list_links()[0]["registry"] == {"property": {"address": "Rua A, 10", "city": "Canoas"},
                                                "consumer": {"name": "Outro", "document": "123"}}
    [released] = repo.list_bills(status=BillStatus.NORMALIZED)
    assert released.consumer == ConsumerInfo(name="Ana", document="123") and released.property_info == prop


def test_banco_antigo_ganha_coluna_de_cadastro() -> None:
    r = Repository.from_url("sqlite+pysqlite:///:memory:")
    with r.engine.begin() as conn:
        conn.execute(text("CREATE TABLE account_links (provider VARCHAR(32), account_number VARCHAR(64), "
                          "property_id VARCHAR(64) NOT NULL, contract_id VARCHAR(64), updated_at TIMESTAMP NOT NULL, "
                          "PRIMARY KEY (provider, account_number))"))
    r.create_schema()
    r.link_account("cpfl", "uc1", "imovel-1", None, consumer=ConsumerInfo(name="Ana"))
    link = r.find_link("cpfl", "uc1")
    assert link is not None and link.consumer == ConsumerInfo(name="Ana")
