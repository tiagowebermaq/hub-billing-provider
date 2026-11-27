"""Situação de pagamento das contas: Em aberto, Vencida e Paga (quando a conta some do portal)."""
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from hub_billing.adapters.base import ProviderAccount, RawBill
from hub_billing.adapters.cpfl import agencia_virtual as av
from hub_billing.api.app import MemoryPublisher
from hub_billing.domain.models import Bill, PaymentStatus, UtilityType
from hub_billing.infra.repository import Repository
from hub_billing.workers.handlers import payment_summary, reconcile_payments

AGORA = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)


@pytest.fixture
def repo() -> Repository:
    r = Repository.from_url("sqlite+pysqlite:///:memory:")
    r.create_schema()
    return r


def conta(account: str, ref: str, due: date, provider: str = "aegea") -> Bill:
    return Bill(provider=provider, utility_type=UtilityType.WATER, account_number=account, property_id="ap-1",
                reference_month=ref, due_date=due, amount=Decimal("50.00"))


def raw(account: str, ref: str, provider: str = "aegea", **payload: object) -> RawBill:
    return RawBill(provider=provider, account_number=account, reference_month=ref, due_date=date(2026, 12, 10),
                   amount=Decimal("50.00"), payload=dict(payload))


def situacao(repo: Repository, account: str, provider: str = "aegea") -> dict[str, str]:
    return {b.reference_month: b.payment_on(date(2026, 11, 27)).value for b in repo.account_bills(provider, account)}


def test_em_aberto_vencida_e_paga() -> None:
    b = conta("m1", "2026-10", date(2026, 11, 10))
    assert b.payment_on(date(2026, 11, 10)) == PaymentStatus.OPEN  # no dia do vencimento ainda está em aberto
    assert b.payment_on(date(2026, 11, 11)) == PaymentStatus.OVERDUE
    assert b.model_copy(update={"paid_at": AGORA}).payment_on(date(2026, 12, 1)) == PaymentStatus.PAID
    assert "payment_status" in b.model_dump(mode="json")


def test_conta_que_sumiu_da_lista_de_abertas_vira_paga(repo: Repository) -> None:
    for ref, due in (("2026-09", date(2026, 10, 10)), ("2026-10", date(2026, 11, 10)), ("2026-11", date(2026, 12, 10))):
        repo.save_bill(conta("m1", ref, due))
    accounts = [ProviderAccount(account_number="m1")]
    changed = reconcile_payments(repo, "aegea", accounts, [raw("m1", "2026-10"), raw("m1", "2026-11")], AGORA)
    assert [b.reference_month for b in changed] == ["2026-09"]
    assert situacao(repo, "m1") == {"2026-09": "PAGA", "2026-10": "VENCIDA", "2026-11": "EM_ABERTO"}
    [setembro] = [b for b in repo.account_bills("aegea", "m1") if b.reference_month == "2026-09"]
    assert setembro.paid_at == AGORA and setembro.payment_source == "portal"
    assert reconcile_payments(repo, "aegea", accounts, [raw("m1", "2026-10"), raw("m1", "2026-11")], AGORA) == []
    # voltou a aparecer em aberto: volta para aberto e avisa
    [reaberta] = reconcile_payments(repo, "aegea", accounts, [raw("m1", "2026-09"), raw("m1", "2026-10"),
                                                              raw("m1", "2026-11")], AGORA)
    assert reaberta.paid_at is None and payment_summary(reaberta)["payment_status"] == "VENCIDA"


def test_so_confere_as_ucs_que_o_portal_listou(repo: Repository) -> None:
    """Conta de outra matrícula (veio por e-mail ou importação, portal não acompanha) não vira paga sozinha."""
    repo.save_bill(conta("m1", "2026-10", date(2026, 11, 10)))
    repo.save_bill(conta("email-9", "2026-10", date(2026, 11, 10)))
    reconcile_payments(repo, "aegea", [ProviderAccount(account_number="m1")], [], AGORA)
    assert situacao(repo, "m1") == {"2026-10": "PAGA"}
    assert situacao(repo, "email-9") == {"2026-10": "VENCIDA"}


def test_status_paga_do_portal_e_marcacao_manual(repo: Repository) -> None:
    repo.save_bill(conta("m1", "2026-10", date(2026, 11, 10)))
    repo.save_bill(conta("m1", "2026-11", date(2026, 12, 10)))
    accounts = [ProviderAccount(account_number="m1")]
    reconcile_payments(repo, "aegea", accounts, [raw("m1", "2026-10", status="Aguardando pagamento"),
                                                 raw("m1", "2026-11", status="PAGA")], AGORA)
    assert situacao(repo, "m1") == {"2026-10": "VENCIDA", "2026-11": "PAGA"}
    [outubro] = [b for b in repo.account_bills("aegea", "m1") if b.reference_month == "2026-10"]
    repo.set_payment(outubro.id, None, "manual", "weber@hub")  # alguém disse no painel que está em aberto
    assert reconcile_payments(repo, "aegea", accounts, [], AGORA) == []
    assert situacao(repo, "m1") == {"2026-10": "VENCIDA", "2026-11": "PAGA"}  # o manual não muda


def _uc(account: str, conta_atual: dict[str, object] | None) -> ProviderAccount:
    return ProviderAccount(account_number=account, extra={"conta_atual": conta_atual})


def test_site_da_cpfl_mostra_uma_conta_so_por_uc(repo: Repository) -> None:
    for ref, due in (("2026-09", date(2026, 10, 20)), ("2026-10", date(2026, 11, 20)), ("2026-11", date(2026, 12, 20))):
        repo.save_bill(conta("uc1", ref, due, "cpfl"))
    atrasada = raw("uc1", "2026-10", "cpfl", source="agencia-virtual", status="atrasada")
    reconcile_payments(repo, "cpfl", [_uc("uc1", {})], [atrasada], AGORA)
    assert situacao(repo, "uc1", "cpfl")["2026-09"] == "VENCIDA"  # a mostrada está atrasada: pode haver mais antigas
    atual = raw("uc1", "2026-10", "cpfl", source="agencia-virtual", status=None)
    reconcile_payments(repo, "cpfl", [_uc("uc1", {})], [atual], AGORA)
    assert situacao(repo, "uc1", "cpfl") == {"2026-09": "PAGA", "2026-10": "VENCIDA", "2026-11": "EM_ABERTO"}
    reconcile_payments(repo, "cpfl", [_uc("uc1", None), _uc("uc2", {})], [raw("uc2", "2026-11", "cpfl")], AGORA)
    assert set(situacao(repo, "uc1", "cpfl").values()) == {"PAGA"}  # UC sem conta atual: tudo pago


def test_nenhuma_uc_com_conta_atual_nao_marca_nada(repo: Repository) -> None:
    repo.save_bill(conta("uc1", "2026-10", date(2026, 11, 20), "cpfl"))
    assert reconcile_payments(repo, "cpfl", [_uc("uc1", None), _uc("uc2", None)], [], AGORA) == []
    assert situacao(repo, "uc1", "cpfl") == {"2026-10": "VENCIDA"}


def test_pagina_sem_card_de_conta_atual() -> None:
    assert av.sem_conta_atual("<html><body><p>Você não possui débitos</p></body></html>")
    assert not av.sem_conta_atual('<div class="coh-column conta-atual"></div>')


def test_painel_marca_como_paga_e_filtra(client: TestClient, admin_headers: dict[str, str],
                                         publisher: MemoryPublisher) -> None:
    repo: Repository = client.app.state.repo  # type: ignore[attr-defined]
    b = conta("m1", "2020-01", date(2020, 2, 10))
    repo.save_bill(b)
    vencidas = client.get("/api/v1/bills", params={"payment": "VENCIDA"}, headers=admin_headers).json()
    assert [x["id"] for x in vencidas] == [b.id] and vencidas[0]["payment_status"] == "VENCIDA"
    resp = client.post(f"/api/v1/bills/{b.id}/payment", json={"paid": True}, headers=admin_headers)
    assert resp.status_code == 200 and resp.json()["payment_status"] == "PAGA"
    assert resp.json()["payment_source"] == "manual" and resp.json()["paid_by"] == "admin@hub.local"
    _, _, env = publisher.sent[-1]
    assert env.type == "bill.paid" and env.payload["bill_id"] == b.id and env.payload["paid_at"]
    assert client.get("/api/v1/bills", params={"payment": "VENCIDA"}, headers=admin_headers).json() == []
    resp = client.post(f"/api/v1/bills/{b.id}/payment", json={"paid": False}, headers=admin_headers)
    assert resp.json()["payment_status"] == "VENCIDA" and publisher.sent[-1][2].type == "bill.reopened"
    assert client.post("/api/v1/bills/nada/payment", json={"paid": True}, headers=admin_headers).status_code == 404
    events = client.get("/api/v1/webhooks/events", headers=admin_headers).json()
    assert "bill.paid" in events and "bill.reopened" in events
    html = client.get("/painel").text
    assert "Marcar como paga" in html and 'name="payment"' in html and "/payment" in html
