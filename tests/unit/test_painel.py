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


def test_painel_tem_modo_claro_e_escuro(client: TestClient) -> None:
    html = client.get("/painel").text
    assert 'prefers-color-scheme:dark' in html  # segue o sistema por padrão
    assert ':root[data-theme="dark"]' in html and ':root:not([data-theme="light"])' in html
    assert 'class="tema"' in html and "hub.tema" in html  # botão e escolha lembrada


def test_painel_pede_troca_de_senha_no_primeiro_acesso(client: TestClient) -> None:
    html = client.get("/painel").text
    assert 'id="senha-form"' in html and "/api/v1/me/password" in html
    assert "me.must_change_password" in html


def test_painel_conecta_portais_sem_terminal(client: TestClient) -> None:
    html = client.get("/painel").text
    assert 'data-provider="cpfl" data-distributor="rge"' in html
    assert 'data-provider="aegea" data-distributor="corsan"' in html
    assert "/connect" in html and "login_portal.py" not in html
