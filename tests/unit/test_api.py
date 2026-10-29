from fastapi.testclient import TestClient

from hub_billing.api.app import MemoryPublisher
from tests.conftest import login


def test_health_echoes_correlation_id(client: TestClient) -> None:
    resp = client.get("/health", headers={"X-Correlation-ID": "abc"})
    assert resp.status_code == 200
    assert resp.headers["X-Correlation-ID"] == "abc"
    assert client.get("/health").headers["X-Correlation-ID"]


def test_admin_created_on_boot_and_must_change_password(client: TestClient) -> None:
    resp = client.post("/api/v1/auth/login", data={"username": "admin@hub.local", "password": "admin-pass"})
    assert resp.json()["must_change_password"] is True
    assert client.post("/api/v1/auth/login", data={"username": "admin@hub.local", "password": "x"}).status_code == 401


def test_requires_token(client: TestClient) -> None:
    assert client.get("/api/v1/me").status_code == 401
    assert client.get("/api/v1/me", headers={"Authorization": "Bearer lixo"}).status_code == 401


def test_change_password(client: TestClient, admin_headers: dict[str, str]) -> None:
    assert client.post("/api/v1/me/password", json={"new_password": "curta"}, headers=admin_headers).status_code == 422
    assert client.post("/api/v1/me/password", json={"new_password": "nova-senha-forte"},
                       headers=admin_headers).status_code == 204
    resp = client.post("/api/v1/auth/login", data={"username": "admin@hub.local", "password": "nova-senha-forte"})
    assert resp.json()["must_change_password"] is False


def test_admin_creates_user_and_grants_permission(client: TestClient, admin_headers: dict[str, str]) -> None:
    resp = client.post("/api/v1/users", headers=admin_headers,
                       json={"email": "ana@example.com", "name": "Ana", "password": "senha-ana-123", "roles": ["consulta"]})
    assert resp.status_code == 201
    user_id = resp.json()["id"]
    ana = login(client, "ana@example.com", "senha-ana-123")

    assert client.get("/api/v1/providers", headers=ana).status_code == 200
    assert client.post("/api/v1/collections", json={"provider": "fake"}, headers=ana).status_code == 403
    assert client.get("/api/v1/users", headers=ana).status_code == 403

    resp = client.patch(f"/api/v1/users/{user_id}/permissions", headers=admin_headers,
                        json={"grant": ["collections:run"]})
    assert "collections:run" in resp.json()["permissions"]
    assert client.post("/api/v1/collections", json={"provider": "fake"}, headers=ana).status_code == 202


def test_user_validation(client: TestClient, admin_headers: dict[str, str]) -> None:
    body = {"email": "b@example.com", "name": "B", "password": "x" * 10, "roles": ["chefe"]}
    assert client.post("/api/v1/users", json=body, headers=admin_headers).status_code == 422
    body["roles"] = []
    assert client.post("/api/v1/users", json=body, headers=admin_headers).status_code == 201
    assert client.post("/api/v1/users", json=body, headers=admin_headers).status_code == 409
    admin_id = client.get("/api/v1/me", headers=admin_headers).json()["id"]
    assert client.patch(f"/api/v1/users/{admin_id}/permissions", json={}, headers=admin_headers).status_code == 409
    assert client.patch("/api/v1/users/nada/permissions", json={}, headers=admin_headers).status_code == 404


def test_collection_publishes_with_correlation_id(
    client: TestClient, admin_headers: dict[str, str], publisher: MemoryPublisher
) -> None:
    resp = client.post("/api/v1/collections", headers={**admin_headers, "X-Correlation-ID": "fluxo-1"},
                       json={"provider": "cpfl", "distributor": "rge", "reference_month": "2026-10"})
    assert resp.status_code == 202
    assert resp.json()["correlation_id"] == "fluxo-1"
    exchange, key, env = publisher.sent[-1]
    assert (exchange, key, env.type, env.correlation_id) == ("hub.collect", "collect.request.cpfl",
                                                             "collect.request", "fluxo-1")
    assert client.post("/api/v1/collections", json={"provider": "x"}, headers=admin_headers).status_code == 422
    assert client.get("/api/v1/collections", headers=admin_headers).json() == []
    client.app.state.repo.record_run("r1", "cpfl", "rge", "login", message="sessão expirada")  # type: ignore[attr-defined]
    [run] = client.get("/api/v1/collections", headers=admin_headers).json()
    assert (run["provider"], run["status"], run["message"]) == ("cpfl", "login", "sessão expirada")


def test_deactivated_user_token_is_rejected(client: TestClient, admin_headers: dict[str, str]) -> None:
    client.post("/api/v1/users", headers=admin_headers,
                json={"email": "c@example.com", "name": "C", "password": "senha-c-12345", "roles": ["consulta"]})
    headers = login(client, "c@example.com", "senha-c-12345")
    store = client.app.state.users  # type: ignore[attr-defined]
    user = store.by_email("c@example.com")
    user.active = False
    store.save(user)
    assert client.get("/api/v1/me", headers=headers).status_code == 401


def test_fake_provider_hidden_outside_dev(settings, publisher) -> None:  # type: ignore[no-untyped-def]
    from hub_billing.api.app import create_app

    prod = TestClient(create_app(settings.model_copy(update={"env": "prod"}), publisher))
    providers = prod.get("/api/v1/providers", headers=login(prod, "admin@hub.local", "admin-pass")).json()
    assert "fake" not in providers and {"cpfl", "aegea", "vivo"} <= set(providers)


def test_password_and_users_survive_restart(tmp_path, settings) -> None:  # type: ignore[no-untyped-def]
    from hub_billing.api.app import create_app
    from hub_billing.infra.repository import Repository

    url = f"sqlite:///{tmp_path / 'hub.db'}"
    client = TestClient(create_app(settings, repo=Repository.from_url(url)))
    admin = login(client, "admin@hub.local", "admin-pass")
    assert client.get("/api/v1/me", headers=admin).json()["must_change_password"] is True
    assert client.post("/api/v1/me/password", json={"new_password": "admin-pass"},
                       headers=admin).status_code == 422  # precisa ser diferente
    assert client.post("/api/v1/me/password", json={"new_password": "nova-senha-forte"},
                       headers=admin).status_code == 204
    assert client.post("/api/v1/users", headers=admin, json={
        "email": "ana@example.com", "name": "Ana", "password": "senha-ana-123", "roles": ["consulta"]}).status_code == 201

    reiniciado = TestClient(create_app(settings, repo=Repository.from_url(url)))
    nova = login(reiniciado, "admin@hub.local", "nova-senha-forte")
    assert reiniciado.get("/api/v1/me", headers=nova).json()["must_change_password"] is False
    assert login(reiniciado, "ana@example.com", "senha-ana-123")


def test_cadastro_aguarda_aprovacao_do_admin(client: TestClient, admin_headers: dict[str, str]) -> None:
    novo = {"name": "Rita", "email": "rita@example.com", "password": "senha-rita-123"}
    assert client.post("/api/v1/auth/signup", json={**novo, "password": "curta"}).status_code == 422
    assert client.post("/api/v1/auth/signup", json={**novo, "name": " "}).status_code == 422
    assert client.post("/api/v1/auth/signup", json=novo).status_code == 201
    assert client.post("/api/v1/auth/signup", json=novo).status_code == 409

    login_rita = {"username": "rita@example.com", "password": "senha-rita-123"}
    resp = client.post("/api/v1/auth/login", data=login_rita)
    assert resp.status_code == 403 and "aprovação" in resp.json()["detail"]
    assert client.post("/api/v1/auth/login", data={**login_rita, "password": "errada"}).status_code == 401

    rita = next(u for u in client.get("/api/v1/users", headers=admin_headers).json() if u["email"] == "rita@example.com")
    assert rita["active"] is False and rita["roles"] == []
    assert client.patch(f"/api/v1/users/{rita['id']}/status", json={"active": True}, headers=admin_headers).json()["active"]
    assert client.post("/api/v1/auth/login", data=login_rita).status_code == 200


def test_admin_redefine_senha_de_usuario(client: TestClient, admin_headers: dict[str, str]) -> None:
    client.post("/api/v1/users", headers=admin_headers,
                json={"email": "leo@example.com", "name": "Leo", "password": "senha-leo-123", "roles": ["consulta"]})
    users = client.get("/api/v1/users", headers=admin_headers).json()
    leo = next(u for u in users if u["email"] == "leo@example.com")
    admin = next(u for u in users if u["is_admin"])

    temp = client.post(f"/api/v1/users/{leo['id']}/reset-password", headers=admin_headers).json()["temporary_password"]
    resp = client.post("/api/v1/auth/login", data={"username": "leo@example.com", "password": temp})
    assert resp.status_code == 200 and resp.json()["must_change_password"] is True

    assert client.post(f"/api/v1/users/{admin['id']}/reset-password", headers=admin_headers).status_code == 409
    assert client.patch(f"/api/v1/users/{admin['id']}/status", json={"active": False},
                        headers=admin_headers).status_code == 409
    assert client.patch("/api/v1/users/nao-existe/status", json={"active": True}, headers=admin_headers).status_code == 404
    consulta = login(client, "leo@example.com", temp)
    assert client.patch(f"/api/v1/users/{leo['id']}/status", json={"active": True}, headers=consulta).status_code == 403


def test_baixa_pdf_da_conta(client: TestClient, admin_headers: dict[str, str]) -> None:
    url = "/api/v1/documents/cpfl/4000000001/2026-10"
    assert client.get(url).status_code == 401
    resp = client.get(url, headers=admin_headers)
    assert resp.status_code == 404 and "coleta" in resp.json()["detail"]
    client.app.state.repo.save_document("cpfl:4000000001:2026-10", b"%PDF-1")  # type: ignore[attr-defined]
    client.app.state.repo.save_document("cpfl:4000000001:2026-10", b"%PDF-2")  # type: ignore[attr-defined]
    resp = client.get(url, headers=admin_headers)
    assert resp.content == b"%PDF-2" and resp.headers["content-type"] == "application/pdf"
    assert 'filename="conta-cpfl-4000000001-2026-10.pdf"' in resp.headers["content-disposition"]
