"""Login assistido nos portais pelo terminal (o painel faz o mesmo com o botão "Conectar" em Portais).

Abre o portal num navegador, você entra com o seu usuário, senha e código de verificação, e o script captura só
o token de acesso da sessão e o envia ao hub, que guarda cifrado. A senha nunca passa pelo script nem pelo hub.

    python tools/login_portal.py cpfl --distributor rge
    python tools/login_portal.py aegea --distributor corsan

Requer: pip install playwright && python -m playwright install chromium
"""
from __future__ import annotations

import argparse
import getpass
import sys
from typing import Any

import httpx

from hub_billing.collection.portal_login import (  # noqa: F401 - reexportados para quem usa o script
    CAPTURE,
    PORTALS,
    aegea_capture,
    capture,
    cpfl_capture,
)


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
    print(f"Entre no portal na janela que vai abrir (usuário, senha e código). Até {args.timeout // 60} min.")
    data = capture(args.provider, args.timeout)
    send(args.hub, email, password, args.provider, args.distributor, data)
    print(f"Sessão de {args.provider} salva no hub (cifrada). Já dá para disparar a coleta.")


if __name__ == "__main__":
    main(sys.argv[1:])
