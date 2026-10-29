from fastapi.testclient import TestClient


def test_raiz_redireciona_para_painel(client: TestClient) -> None:
    res = client.get("/", follow_redirects=False)
    assert res.status_code == 307
    assert res.headers["location"] == "/painel"


def test_painel_e_pagina_sem_dependencias_externas(client: TestClient) -> None:
    res = client.get("/painel")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/html")
    html = res.text
    assert "/api/v1/auth/login" in html and "/api/v1/bills" in html
    assert "https://" not in html  # nada de CDN: funciona offline e na rede da empresa
