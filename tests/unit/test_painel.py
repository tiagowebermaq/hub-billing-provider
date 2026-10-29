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


def test_login_no_novo_visual(client: TestClient) -> None:
    html = client.get("/painel").text
    for trecho in ('placeholder="exemplo@gmail.com"', 'id="lembrar"', "Lembrar de mim", "Esqueceu sua senha?",
                   "Ainda não tem uma conta?", "Cadastre-se", 'class="olho"', "/api/v1/auth/signup",
                   'data-tab="usuarios"', "/reset-password"):
        assert trecho in html, trecho


def test_painel_avisa_quando_o_hub_nao_responde(client: TestClient) -> None:
    html = client.get("/painel").text
    assert "O hub não respondeu" in html and "falhas > 5" in html  # sem "Failed to fetch" cru na tela


def test_painel_mostra_ultimas_coletas_e_escolhe_o_portal(client: TestClient) -> None:
    html = client.get("/painel").text
    assert 'id="t-coletas"' in html and "/api/v1/collections" in html
    assert 'value="cpfl:rge"' in html and 'value="aegea:corsan"' in html and 'value="todos"' in html


def test_painel_oferece_pdf_pix_e_codigo_para_pagar(client: TestClient) -> None:
    html = client.get("/painel").text
    assert "data-pdf=" in html and "data-copiar=" in html and 'id="m-faturas"' in html
    assert "URL.createObjectURL" in html and "Authorization: `Bearer ${token}`" in html
