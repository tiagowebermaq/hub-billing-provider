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
