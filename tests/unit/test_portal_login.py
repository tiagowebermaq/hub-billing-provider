import threading
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from hub_billing.api.app import create_app
from hub_billing.collection.portal_login import LoginTimeout, PortalLogins
from hub_billing.core.config import Settings
from tests.conftest import login


def esperar(logins: PortalLogins, job_id: str) -> str:
    for _ in range(200):
        job = logins.get(job_id)
        assert job is not None
        if job.status != "aguardando":
            return job.status
        time.sleep(0.01)
    raise AssertionError("login não terminou")


def test_login_concluido_salva_sessao() -> None:
    salvos: list[tuple[Any, ...]] = []
    logins = PortalLogins(lambda *a: salvos.append(a), lambda provider, timeout: {"access_token": "at"})
    job = logins.start("cpfl", "rge")
    assert esperar(logins, job.id) == "conectado"
    assert salvos == [("cpfl", "rge", {"access_token": "at"})]


def test_login_com_erro_mostra_motivo() -> None:
    def falha(provider: str, timeout: int) -> dict[str, Any]:
        raise LoginTimeout("a janela do portal foi fechada antes de concluir o login")

    def sem_playwright(provider: str, timeout: int) -> dict[str, Any]:
        raise ImportError("playwright")

    logins = PortalLogins(lambda *a: None, falha)
    job = logins.start("aegea", "corsan")
    assert esperar(logins, job.id) == "erro"
    assert "fechada" in (logins.get(job.id).error or "")  # type: ignore[union-attr]

    logins = PortalLogins(lambda *a: None, sem_playwright)
    job = logins.start("aegea", None)
    assert esperar(logins, job.id) == "erro"
    assert "iniciar.bat" in (logins.get(job.id).error or "")  # type: ignore[union-attr]


def test_um_login_por_portal_e_portal_conhecido() -> None:
    liberar = threading.Event()

    def demora(provider: str, timeout: int) -> dict[str, Any]:
        liberar.wait(2)
        return {"access_token": "at"}

    logins = PortalLogins(lambda *a: None, demora)
    job = logins.start("cpfl", "rge")
    with pytest.raises(RuntimeError):
        logins.start("cpfl", "rge")
    with pytest.raises(ValueError):
        logins.start("vivo", None)
    liberar.set()
    assert esperar(logins, job.id) == "conectado"
    assert logins.get("nao-existe") is None


def test_api_conecta_portal_pelo_painel(settings: Settings) -> None:
    capture = lambda provider, timeout: {"access_token": "at", "expires_at": "2026-12-01T00:00:00+00:00"}  # noqa: E731
    client = TestClient(create_app(settings, portal_capture=capture))
    admin = login(client, "admin@hub.local", "admin-pass")

    resp = client.post("/api/v1/providers/aegea/connect", json={"distributor": "corsan"}, headers=admin)
    assert resp.status_code == 202
    job_id = resp.json()["id"]
    assert esperar(client.app.state.portal_logins, job_id) == "conectado"  # type: ignore[attr-defined]
    assert client.get(f"/api/v1/providers/connect/{job_id}", headers=admin).json()["status"] == "conectado"
    sessions = client.get("/api/v1/providers/sessions", headers=admin).json()
    assert [(s["provider"], s["distributor"]) for s in sessions] == [("aegea", "corsan")]

    assert client.post("/api/v1/providers/vivo/connect", json={}, headers=admin).status_code == 422
    assert client.get("/api/v1/providers/connect/xyz", headers=admin).status_code == 404
