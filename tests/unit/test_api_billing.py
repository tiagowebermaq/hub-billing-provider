from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient

from hub_billing.api.app import MemoryPublisher
from hub_billing.domain.models import Bill, UtilityType


def seed(client: TestClient, account: str, prop: str | None) -> None:
    repo = client.app.state.repo  # type: ignore[attr-defined]
    repo.save_bill(Bill(provider="aegea", distributor="corsan", utility_type=UtilityType.WATER, account_number=account,
                        property_id=prop, reference_month="2026-10", due_date=date(2026, 11, 10), amount=Decimal("80.10")))


def test_vincula_conta_e_libera_revisao_manual(client: TestClient, admin_headers: dict[str, str]) -> None:
    seed(client, "mat-1", None)
    pending = client.get("/api/v1/bills", params={"status": "MANUAL_REVIEW"}, headers=admin_headers).json()
    assert [b["account_number"] for b in pending] == ["mat-1"]
    resp = client.put("/api/v1/accounts/aegea/mat-1", json={"property_id": "ap-101", "contract_id": "c-7"}, headers=admin_headers)
    assert resp.status_code == 200 and resp.json()["bills_released"] == 1
    assert client.get("/api/v1/accounts", headers=admin_headers).json()[0]["property_id"] == "ap-101"
    assert client.put("/api/v1/accounts/xpto/1", json={"property_id": "x"}, headers=admin_headers).status_code == 422


def test_fecha_lotes_e_reenvia(client: TestClient, admin_headers: dict[str, str], publisher: MemoryPublisher) -> None:
    seed(client, "mat-1", "ap-101")
    seed(client, "mat-2", "ap-101")
    seed(client, "mat-3", None)
    resp = client.post("/api/v1/batches/close", json={"reference_month": "2026-10"}, headers=admin_headers)
    body = resp.json()
    assert resp.status_code == 202 and body["bills"] == 2 and body["pending_manual_review"] == 1
    [batch_id] = body["batches"]
    _, key, env = publisher.sent[-1]
    assert key == "batch.ready" and env.payload == {"batch_id": batch_id, "total": "160.20"}
    listed = client.get("/api/v1/batches", params={"reference_month": "2026-10"}, headers=admin_headers).json()
    assert [(b["id"], b["status"]) for b in listed] == [(batch_id, "READY")]
    again = client.post("/api/v1/batches/close", json={"reference_month": "2026-10"}, headers=admin_headers).json()
    assert again["batches"] == []
    assert client.post(f"/api/v1/batches/{batch_id}/resend", headers=admin_headers).status_code == 202
    assert publisher.sent[-1][2].producer == "api.resend"
    assert client.post("/api/v1/batches/nada/resend", headers=admin_headers).status_code == 404


def test_consulta_nao_fecha_lote(client: TestClient, admin_headers: dict[str, str]) -> None:
    from tests.conftest import login

    client.post("/api/v1/users", json={"email": "carla@example.com", "name": "Carla",
                                       "password": "senha-forte-1", "roles": ["consulta"]},
                headers=admin_headers)
    headers = login(client, "carla@example.com", "senha-forte-1")
    assert client.get("/api/v1/bills", headers=headers).status_code == 200
    assert client.post("/api/v1/batches/close", json={"reference_month": "2026-10"}, headers=headers).status_code == 403
