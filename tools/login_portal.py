"""Login assistido nos portais das concessionárias (roda no PC do operador, com tela).

Abre o portal num navegador, você entra com o seu usuário, senha e código de verificação, e o script captura só
o token de acesso da sessão e o envia ao hub, que guarda cifrado. A senha nunca passa pelo script nem pelo hub.

    python tools/login_portal.py cpfl --distributor rge
    python tools/login_portal.py aegea --distributor corsan

Requer: pip install playwright && python -m playwright install chromium
"""
from __future__ import annotations

import argparse
import getpass
import json
import sys
import time
from datetime import UTC, datetime
from typing import Any

import httpx

PORTALS = {
    "cpfl": "https://servicosonline.cpfl.com.br/agencia-webapp/",
    "aegea": "https://cliente.corsan.com.br/entrar",
}
CPFL_COOKIE_DOMAIN = "cpfl.com.br"


def cpfl_capture(page: Any, context: Any) -> dict[str, Any] | None:
    """A Agência Virtual CPFL guarda a resposta inteira do /api/token em sessionStorage['userSessionStorage']."""
    raw = page.evaluate("() => window.sessionStorage.getItem('userSessionStorage')")
    if not raw:
        return None
    data: dict[str, Any] = json.loads(raw)
    if not data.get("access_token"):
        return None
    data["user_agent"] = page.evaluate("() => navigator.userAgent")
    data["cookies"] = {c["name"]: c["value"] for c in context.cookies() if CPFL_COOKIE_DOMAIN in c["domain"]}
    return data


def aegea_capture(page: Any, context: Any) -> dict[str, Any] | None:
    """O portal da Corsan usa MSAL com cache no localStorage: pega o access token da API e o refresh token."""
    entries = page.evaluate("() => Object.keys(localStorage).map(k => [k, localStorage.getItem(k)])")
    access: dict[str, Any] | None = None
    refresh: dict[str, Any] | None = None
    for _, value in entries:
        try:
            item = json.loads(value)
        except (TypeError, ValueError):
            continue
        if not isinstance(item, dict):
            continue
        kind = str(item.get("credentialType", ""))
        if kind == "AccessToken" and "AguasAPI" in str(item.get("target", "")):
            access = item
        elif kind == "RefreshToken":
            refresh = item
    if access is None:
        return None
    expires = datetime.fromtimestamp(int(access.get("expiresOn") or access.get("expires_on") or 0), UTC)
    return {"access_token": access["secret"], "refresh_token": (refresh or {}).get("secret"),
            "expires_at": expires.isoformat()}


CAPTURE = {"cpfl": cpfl_capture, "aegea": aegea_capture}


def capture(provider: str, timeout_s: int) -> dict[str, Any]:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        context = browser.new_context(locale="pt-BR")
        page = context.new_page()
        page.goto(PORTALS[provider])
        print(f"Entre no portal na janela que abriu (usuário, senha e código). Aguardando até {timeout_s // 60} min...")
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            for p in context.pages:
                try:
                    data = CAPTURE[provider](p, context)
                except Exception:  # página navegando entre o portal e o login da Microsoft
                    data = None
                if data:
                    browser.close()
                    return data
            time.sleep(2)
        browser.close()
    raise SystemExit("tempo esgotado: o login não foi concluído")


def send(hub: str, email: str, password: str, provider: str, distributor: str | None, data: dict[str, Any]) -> None:
    with httpx.Client(base_url=hub, timeout=30) as client:
        resp = client.post("/api/v1/auth/login", data={"username": email, "password": password})
        resp.raise_for_status()
        token = resp.json()["access_token"]
        resp = client.put(f"/api/v1/providers/{provider}/session", headers={"Authorization": f"Bearer {token}"},
                          json={"distributor": distributor, "data": data, "expires_at": data.get("expires_at")})
        resp.raise_for_status()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("provider", choices=sorted(PORTALS))
    parser.add_argument("--distributor")
    parser.add_argument("--hub", default="http://localhost:8010", help="endereço da API do hub")
    parser.add_argument("--email", help="usuário do hub com a permissão providers:credentials:write")
    parser.add_argument("--timeout", type=int, default=600, help="segundos para concluir o login")
    args = parser.parse_args(argv)
    email = args.email or input("E-mail do hub: ")
    password = getpass.getpass("Senha do hub: ")
    data = capture(args.provider, args.timeout)
    send(args.hub, email, password, args.provider, args.distributor, data)
    print(f"Sessão de {args.provider} salva no hub (cifrada). Já dá para disparar a coleta.")


if __name__ == "__main__":
    main(sys.argv[1:])
