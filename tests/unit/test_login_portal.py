import json
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import login_portal  # noqa: E402

from hub_billing.collection.portal_login import Seen, snapshot_pages, storage_keys, write_diagnostic  # noqa: E402


class FakePage:
    url = "https://www.cpfl.com.br/agencia/area-cliente/cadastro"

    def __init__(self, session: dict[str, str] | None = None, local: dict[str, str] | None = None) -> None:
        self.session = session or {}
        self.local = local or {}

    def evaluate(self, script: str) -> Any:
        if "userAgent" in script:
            return "Mozilla/5.0"
        return [["session", k, v] for k, v in self.session.items()] + [["local", k, v] for k, v in self.local.items()]


class FakeContext:
    def cookies(self) -> list[dict[str, str]]:
        return [{"name": "cf_clearance", "value": "abc", "domain": ".cpfl.com.br"},
                {"name": "outro", "value": "x", "domain": ".google.com"}]


def test_captura_cpfl() -> None:
    assert login_portal.cpfl_capture(FakePage(), FakeContext()) is None
    assert login_portal.cpfl_capture(FakePage({"userSessionStorage": "{}"}), FakeContext()) is None
    sem_uc = FakePage({"userSessionStorage": json.dumps({"access_token": "at", "refresh_token": "rt"})})
    assert login_portal.cpfl_capture(sem_uc, FakeContext()) is None  # ainda não escolheu a unidade consumidora
    page = FakePage({"userSessionStorage": json.dumps({"access_token": "at", "refresh_token": "rt", "Instalacao": "{}"})})
    data = login_portal.cpfl_capture(page, FakeContext())
    assert data == {"access_token": "at", "refresh_token": "rt", "Instalacao": "{}", "user_agent": "Mozilla/5.0",
                    "cookies": {"cf_clearance": "abc"}}


def test_captura_cpfl_site_novo_com_outra_chave() -> None:
    page = FakePage(local={"lixo": "x", "agencia:sessao": json.dumps({"access_token": "at", "ListaInstalacoes": "[]"})})
    data = login_portal.cpfl_capture(page, FakeContext())
    assert data is not None and data["access_token"] == "at"


def test_captura_cpfl_pela_rede() -> None:
    seen = Seen()
    seen.response("https://www.cpfl.com.br/logo.png", "GET", 200, False, dict, "image")
    seen.response("https://outra-api.net/sessao", "GET", 200, True, lambda: [{"Nome": "x"}], "xhr")
    seen.response("https://api.cpfl.com.br/agencia-webapi/api/token", "POST", 200, False,
                  lambda: {"accessToken": "at", "Instalacao": "{}"})
    seen.response("https://api.cpfl.com.br/agencia-webapi/api/token", "POST", 200, False, lambda: json.loads("x"))
    seen.response("https://api.cpfl.com.br/agencia-webapi/api/token", "POST", 200, False, lambda: {"erro": 1})
    seen.response("https://api.cpfl.com.br/agencia-webapi/api/token", "POST", 200, False, lambda: {"erro": 1})
    data = login_portal.cpfl_capture(FakePage(), FakeContext(), seen)
    assert data is not None and data["api_base"] == "https://api.cpfl.com.br/agencia-webapi/api"
    assert data["origin"] == "https://www.cpfl.com.br" and data["referer"] == FakePage.url
    assert seen.calls == ["GET outra-api.net/sessao 200 (com token) [1] {Nome}",
                          "POST api.cpfl.com.br/agencia-webapi/api/token 200 {Instalacao, accessToken}",
                          "POST api.cpfl.com.br/agencia-webapi/api/token 200",
                          "POST api.cpfl.com.br/agencia-webapi/api/token 200 {erro}"]


def test_diagnostico_nao_guarda_tokens(tmp_path: Path) -> None:
    seen = Seen()
    seen.response("https://api.cpfl.com.br/api/token?code=segredo", "POST", 200, True, lambda: {"access_token": "at"})
    page = FakePage({"userSessionStorage": json.dumps({"access_token": "segredo2"}), "simples": "abc"})
    path = write_diagnostic("cpfl", seen, storage_keys(page), tmp_path)
    texto = path.read_text(encoding="utf-8")
    assert "POST api.cpfl.com.br/api/token 200 (com token) {access_token}" in texto
    assert "session:userSessionStorage {access_token}" in texto and "session:simples\n" in texto
    assert "segredo" not in texto and "abc" not in texto


def test_captura_aegea() -> None:
    local = {"lixo": "nao-json", "lista": "[1]", "id": json.dumps({"credentialType": "IdToken", "secret": "id"}),
             "rt": json.dumps({"credentialType": "RefreshToken", "secret": "rt"}),
             "outro-at": json.dumps({"credentialType": "AccessToken", "target": "openid", "secret": "x"})}
    assert login_portal.aegea_capture(FakePage(local=local), FakeContext()) is None
    local["at"] = json.dumps({"credentialType": "AccessToken", "target": "https://x/AguasAPI.ReadWrite", "secret": "at",
                              "expiresOn": "1793232000"})
    data = login_portal.aegea_capture(FakePage(local=local), FakeContext())
    assert data is not None and data["access_token"] == "at" and data["refresh_token"] == "rt"
    assert data["expires_at"].startswith("2026-10-29")


@respx.mock
def test_envia_sessao_ao_hub() -> None:
    respx.post("http://hub/api/v1/auth/login").mock(return_value=httpx.Response(200, json={"access_token": "jwt"}))
    put = respx.put("http://hub/api/v1/providers/aegea/session").mock(return_value=httpx.Response(204))
    login_portal.send("http://hub", "admin@x.com", "senha", "aegea", "corsan", {"access_token": "at", "expires_at": None})
    assert put.calls.last.request.headers["Authorization"] == "Bearer jwt"
    assert json.loads(put.calls.last.request.content)["distributor"] == "corsan"


def test_main_fluxo_completo(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    sent: list[tuple[Any, ...]] = []
    monkeypatch.setattr(login_portal.getpass, "getpass", lambda _: "senha")
    monkeypatch.setattr(login_portal, "capture", lambda p, t: {"access_token": "at"})
    monkeypatch.setattr(login_portal, "send", lambda *a: sent.append(a))
    login_portal.main(["cpfl", "--distributor", "rge", "--email", "admin@x.com"])
    assert sent[0][3:5] == ("cpfl", "rge")
    assert "salva no hub" in capsys.readouterr().out


class SitePage(FakePage):
    url = "https://www.cpfl.com.br/agencia-virtual/pagina-inicial"

    def __init__(self) -> None:
        super().__init__()
        self.visited: list[str] = []

    def evaluate(self, script: str) -> Any:
        if "querySelectorAll" in script:
            return [["Débito e segunda via", "https://www.cpfl.com.br/agencia-virtual/debito"],
                    ["Ver conta completa", "https://www.cpfl.com.br/quebra"]]
        return super().evaluate(script)

    def goto(self, href: str) -> None:
        if "quebra" in href:
            raise RuntimeError("não abriu")
        self.visited.append(href)

    def content(self) -> str:
        return f"<html>{len(self.visited)}</html>"


def test_captura_cpfl_site_novo_por_cookies() -> None:
    cadastro = FakePage()  # ainda escolhendo a UC
    assert login_portal.cpfl_capture(cadastro, FakeContext()) is None
    data = login_portal.cpfl_capture(SitePage(), FakeContext())
    assert data == {"site": "agencia-virtual", "home": SitePage.url, "user_agent": "Mozilla/5.0",
                    "cookies": {"cf_clearance": "abc"}}

    class SemCookies(FakeContext):
        def cookies(self) -> list[dict[str, str]]:
            return []

    assert login_portal.cpfl_capture(SitePage(), SemCookies()) is None


def test_guarda_paginas_do_site_novo(tmp_path: Path) -> None:
    page = SitePage()
    saved = snapshot_pages(page, tmp_path / "paginas")
    assert [p.name for p in saved] == ["00-pagina-inicial.html", "01-debito-e-segunda-via.html"]
    assert page.visited == ["https://www.cpfl.com.br/agencia-virtual/debito"]
    assert saved[1].read_text(encoding="utf-8") == "<html>1</html>"
