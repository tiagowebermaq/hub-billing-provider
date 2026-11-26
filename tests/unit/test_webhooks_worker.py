from collections.abc import Iterator
from datetime import date
from decimal import Decimal

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from hub_billing.api.app import MemoryPublisher
from hub_billing.domain.models import Bill, UtilityType
from hub_billing.infra.repository import Repository
from hub_billing.messaging import topology
from hub_billing.messaging.envelope import Envelope
from hub_billing.webhooks.dispatcher import DeliveryError
from hub_billing.webhooks.signing import verify
from hub_billing.webhooks.worker import deliver_one, delivery_id, fan_out
from hub_billing.workers.handlers import store_bill

URL = "https://locacao.example/hook"


@pytest.fixture
def repo() -> Iterator[Repository]:
    r = Repository.from_url("sqlite+pysqlite:///:memory:")
    r.create_schema()
    yield r
    r.engine.dispose()


def stored_event(repo: Repository) -> Envelope:
    bill = Bill(provider="cpfl", distributor="rge", utility_type=UtilityType.ENERGY, account_number="40012345",
                property_id="imovel-1", reference_month="2026-10", due_date=date(2026, 11, 20), amount=Decimal("150.00"))
    [env] = store_bill(Envelope(type="bill.normalized", producer="n", correlation_id="cid-conta",
                                payload={"bill": bill.model_dump(mode="json")}), repo)
    return env


def test_fan_out_so_para_quem_quer_o_evento(repo: Repository) -> None:
    repo.add_subscription("s1", "Locação", URL, "seg", ["bill.stored"])
    repo.add_subscription("s2", "Financeiro", "https://fin.example/h", "x", ["batch.sent"])
    repo.add_subscription("s3", "Tudo", "https://tudo.example/h", "y", ["*"])
    repo.add_subscription("s4", "Pausado", "https://p.example/h", "z", ["*"])
    repo.set_subscription("s4", active=False)
    event = stored_event(repo)
    outs = fan_out(event, repo)
    assert sorted(o.payload["subscription_id"] for o in outs) == ["s1", "s3"]
    assert all(o.type == "webhook.delivery" and o.correlation_id == "cid-conta" for o in outs)
    assert {d["status"] for d in repo.list_deliveries()} == {"pendente"}


@respx.mock
async def test_entrega_assinada_e_nao_repete(repo: Repository) -> None:
    repo.add_subscription("s1", "Locação", URL, "segredo", ["bill.stored"])
    event = stored_event(repo)
    [out] = fan_out(event, repo)
    route = respx.post(URL).mock(return_value=httpx.Response(204))
    async with httpx.AsyncClient() as http:
        await deliver_one(out, repo, http)
        await deliver_one(out, repo, http)  # mensagem repetida: não entrega de novo
    assert route.call_count == 1
    req = route.calls.last.request
    ts = int(req.headers["X-Hub-Timestamp"])
    assert verify("segredo", ts, req.content, req.headers["X-Hub-Signature-256"], now=ts)
    assert req.headers["X-Hub-Delivery"] == event.message_id and req.headers["X-Correlation-ID"] == "cid-conta"
    assert repo.delivery_status(delivery_id(event, "s1")) == "entregue"
    assert b'"links"' in req.content and b"/api/v1/bills/" in req.content


@respx.mock
async def test_falha_registra_e_sobe_para_retry(repo: Repository) -> None:
    repo.add_subscription("s1", "Locação", URL, "segredo", ["bill.stored"])
    [out] = fan_out(stored_event(repo), repo)
    respx.post(URL).mock(return_value=httpx.Response(503))
    async with httpx.AsyncClient() as http:
        with pytest.raises(DeliveryError):
            await deliver_one(out.next_attempt(), repo, http)
    [row] = repo.list_deliveries()
    assert (row["status"], row["attempts"]) == ("falhou", 2) and "503" in row["error"]


async def test_assinatura_removida_nao_entrega(repo: Repository) -> None:
    repo.add_subscription("s1", "Locação", URL, "segredo", ["bill.stored"])
    [out] = fan_out(stored_event(repo), repo)
    repo.set_subscription("s1", delete_it=True)
    async with httpx.AsyncClient() as http:
        await deliver_one(out, repo, http)  # sem rede: só não faz nada


def test_api_cadastra_testa_pausa_e_remove(client: TestClient, admin_headers: dict[str, str],
                                          publisher: MemoryPublisher) -> None:
    events = client.get("/api/v1/webhooks/events", headers=admin_headers).json()
    assert "bill.stored" in events
    bad = {"name": "X", "url": "ftp://x", "events": ["bill.stored"]}
    assert client.post("/api/v1/webhooks", headers=admin_headers, json=bad).status_code == 422
    assert client.post("/api/v1/webhooks", headers=admin_headers,
                       json={**bad, "url": URL, "events": ["nada"]}).status_code == 422
    assert client.post("/api/v1/webhooks", headers=admin_headers, json={**bad, "url": URL, "events": []}).status_code == 422
    assert client.post("/api/v1/webhooks", headers=admin_headers,
                       json={**bad, "name": " ", "url": URL}).status_code == 422
    res = client.post("/api/v1/webhooks", headers=admin_headers,
                      json={"name": "Locação", "url": URL, "events": ["bill.stored", "batch.sent"]})
    assert res.status_code == 201 and len(res.json()["secret"]) > 30
    sub_id = res.json()["id"]
    [listed] = client.get("/api/v1/webhooks", headers=admin_headers).json()
    assert "secret" not in listed and "secret_enc" not in listed and listed["active"] is True
    assert client.post(f"/api/v1/webhooks/{sub_id}/test", headers=admin_headers).status_code == 202
    exchange, key, env = publisher.sent[-1]
    assert (exchange, key, env.payload["event"]["type"]) == (topology.EX_WEBHOOKS, "webhook.delivery", "webhook.ping")
    assert client.get("/api/v1/webhooks/deliveries", headers=admin_headers).json()[0]["status"] == "pendente"
    assert client.patch(f"/api/v1/webhooks/{sub_id}", headers=admin_headers, json={"active": False}).status_code == 204
    assert client.get("/api/v1/webhooks", headers=admin_headers).json()[0]["active"] is False
    assert client.delete(f"/api/v1/webhooks/{sub_id}", headers=admin_headers).status_code == 204
    assert client.delete(f"/api/v1/webhooks/{sub_id}", headers=admin_headers).status_code == 404
    assert client.post(f"/api/v1/webhooks/{sub_id}/test", headers=admin_headers).status_code == 404
    assert client.patch(f"/api/v1/webhooks/{sub_id}", headers=admin_headers, json={"active": True}).status_code == 404


def test_api_conta_pelo_link_do_webhook(client: TestClient, admin_headers: dict[str, str]) -> None:
    env = stored_event(client.app.state.repo)  # type: ignore[attr-defined]
    res = client.get(env.payload["links"]["bill"], headers=admin_headers)
    assert res.status_code == 200 and res.json()["account_number"] == "40012345"
    assert client.get("/api/v1/bills/nao-existe", headers=admin_headers).status_code == 404
