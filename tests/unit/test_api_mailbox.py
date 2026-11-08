import pytest
from fastapi.testclient import TestClient

from hub_billing.adapters.email_inbox.adapter import parse_message
from hub_billing.api.app import MemoryPublisher, create_app
from hub_billing.core.config import Settings
from hub_billing.messaging import topology
from tests.conftest import login
from tests.unit.emails import FakeMailClient, make_email

BOX = {"host": "imap.heimber.com.br", "username": "41u6u31@heimber.com.br", "password": "segredo-da-caixa"}


@pytest.fixture
def box() -> FakeMailClient:
    return FakeMailClient({"1": make_email(), "2": make_email(message_id="<m2@x>")})


@pytest.fixture
def client(settings: Settings, publisher: MemoryPublisher, box: FakeMailClient) -> TestClient:
    return TestClient(create_app(settings, publisher, mail_client=box))


def test_cadastro_da_caixa_sem_devolver_senhas(client: TestClient, admin_headers: dict[str, str]) -> None:
    start = client.get("/api/v1/mailbox", headers=admin_headers).json()
    assert start["configured"] is False and start["config"]["username"] == "41u6u31@heimber.com.br"
    res = client.put("/api/v1/mailbox", headers=admin_headers, json={**BOX, "pdf_passwords": ["12345", " "]})
    assert res.status_code == 204
    body = client.get("/api/v1/mailbox", headers=admin_headers).json()
    assert body["configured"] is True and body["config"]["has_password"] is True
    assert body["config"]["pdf_passwords"] == 1 and body["state"]["from_panel"] is True
    assert "segredo-da-caixa" not in str(body) and "12345" not in str(body)
    # senha omitida mantém a salva
    assert client.put("/api/v1/mailbox", headers=admin_headers, json={**BOX, "password": None, "folder": "Faturas"}
                      ).status_code == 204
    again = client.get("/api/v1/mailbox", headers=admin_headers).json()
    assert again["config"]["has_password"] is True and again["config"]["folder"] == "Faturas"
    assert again["config"]["pdf_passwords"] == 1


def test_cadastro_valida_campos(client: TestClient, admin_headers: dict[str, str]) -> None:
    assert client.put("/api/v1/mailbox", headers=admin_headers, json={**BOX, "host": " "}).status_code == 422
    assert client.put("/api/v1/mailbox", headers=admin_headers, json={**BOX, "poll_seconds": 10}).status_code == 422


def test_testar_conexao(client: TestClient, admin_headers: dict[str, str], box: FakeMailClient) -> None:
    assert client.post("/api/v1/mailbox/test", headers=admin_headers).status_code == 422  # sem cadastro
    client.put("/api/v1/mailbox", headers=admin_headers, json=BOX)
    ok = client.post("/api/v1/mailbox/test", headers=admin_headers).json()
    assert ok["ok"] is True and ok["unseen"] == 2 and box.seen == set()  # só conta, não marca nada
    assert box.config.password == "segredo-da-caixa"
    box.fail_on = "open"
    fail = client.post("/api/v1/mailbox/test", headers=admin_headers).json()
    assert fail == {"ok": False, "detail": "falha simulada em open"}


def test_verificar_agora_publica_email_poll(client: TestClient, admin_headers: dict[str, str],
                                            publisher: MemoryPublisher) -> None:
    res = client.post("/api/v1/mailbox/check", headers={**admin_headers, "X-Correlation-ID": "cid-check"})
    assert res.status_code == 202 and res.json()["correlation_id"] == "cid-check"
    exchange, key, env = publisher.sent[-1]
    assert (exchange, key, env.type) == (topology.EX_EMAIL, "email.poll", "email.poll")


def test_lista_e_reprocessa_emails(client: TestClient, admin_headers: dict[str, str],
                                   publisher: MemoryPublisher) -> None:
    repo = client.app.state.repo  # type: ignore[attr-defined]
    raw = make_email()
    email = parse_message(raw)
    repo.save_email(email.id, email.message_id, email.sender, email.subject, email.received_at, raw, "cid-e")
    repo.set_email_status(email.id, "revisao", "f.pdf: PDF protegido por senha")
    [row] = client.get("/api/v1/mailbox/emails", headers=admin_headers).json()
    assert row["status"] == "revisao" and "raw" not in row and row["sender"] == "fatura@rge-rs.com.br"
    res = client.post(f"/api/v1/mailbox/emails/{email.id}/reprocess", headers=admin_headers)
    assert res.status_code == 202 and res.json()["correlation_id"] == "cid-e"
    _, key, env = publisher.sent[-1]
    assert key == "email.received" and env.payload["force"] is True and env.payload["email_id"] == email.id
    assert client.post("/api/v1/mailbox/emails/nao-existe/reprocess", headers=admin_headers).status_code == 404


def test_operador_ve_emails_mas_nao_a_senha_da_caixa(client: TestClient, admin_headers: dict[str, str]) -> None:
    client.post("/api/v1/users", headers=admin_headers,
                json={"email": "op@example.com", "name": "Op", "password": "senha-do-operador", "roles": ["operador"]})
    op = login(client, "op@example.com", "senha-do-operador")
    assert client.get("/api/v1/mailbox", headers=op).status_code == 403
    assert client.put("/api/v1/mailbox", headers=op, json=BOX).status_code == 403
    assert client.get("/api/v1/mailbox/emails", headers=op).status_code == 200
    assert client.post("/api/v1/mailbox/check", headers=op).status_code == 202
