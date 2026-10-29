import json
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import login_portal  # noqa: E402


class FakePage:
    def __init__(self, session: dict[str, str] | None = None, local: dict[str, str] | None = None) -> None:
        self.session = session or {}
        self.local = local or {}

    def evaluate(self, script: str) -> Any:
        if "userSessionStorage" in script:
            return self.session.get("userSessionStorage")
        if "userAgent" in script:
            return "Mozilla/5.0"
        return list(self.local.items())


class FakeContext:
    def cookies(self) -> list[dict[str, str]]:
        return [{"name": "cf_clearance", "value": "abc", "domain": ".cpfl.com.br"},
                {"name": "outro", "value": "x", "domain": ".google.com"}]


def test_captura_cpfl() -> None:
    assert login_portal.cpfl_capture(FakePage(), FakeContext()) is None
    assert login_portal.cpfl_capture(FakePage({"userSessionStorage": "{}"}), FakeContext()) is None
    page = FakePage({"userSessionStorage": json.dumps({"access_token": "at", "refresh_token": "rt"})})
    data = login_portal.cpfl_capture(page, FakeContext())
    assert data == {"access_token": "at", "refresh_token": "rt", "user_agent": "Mozilla/5.0", "cookies": {"cf_clearance": "abc"}}


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
