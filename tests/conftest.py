import pytest
from fastapi.testclient import TestClient

from hub_billing.api.app import MemoryPublisher, create_app
from hub_billing.core.config import Settings


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None, env="dev", jwt_secret="test-secret-with-at-least-32-bytes!!", admin_email="admin@hub.local",
                    admin_password="admin-pass", database_url="sqlite+pysqlite:///:memory:")


@pytest.fixture
def publisher() -> MemoryPublisher:
    return MemoryPublisher()


@pytest.fixture
def client(settings: Settings, publisher: MemoryPublisher) -> TestClient:
    return TestClient(create_app(settings, publisher))


def login(client: TestClient, email: str, password: str) -> dict[str, str]:
    resp = client.post("/api/v1/auth/login", data={"username": email, "password": password})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture
def admin_headers(client: TestClient) -> dict[str, str]:
    return login(client, "admin@hub.local", "admin-pass")
