"""Volta a conta admin do hub para o e-mail e a senha do .env, para quando a senha foi esquecida.

O painel pede uma senha nova no próximo acesso. Rode na pasta do hub (ou use resetar-senha.bat):
    python tools/reset_admin.py
"""
from __future__ import annotations

from hub_billing.api.users import UserStore
from hub_billing.core.config import Settings, get_settings
from hub_billing.infra.repository import make_engine


def reset(settings: Settings) -> str:
    admin = UserStore(make_engine(settings.database_url)).reset_admin(settings.admin_email, settings.admin_password)
    return admin.email


def main() -> None:
    email = reset(get_settings())
    print(f"Admin redefinido. Entre no painel com {email} e a senha HUB_ADMIN_PASSWORD do .env;")
    print("em seguida o painel vai pedir uma senha nova.")


if __name__ == "__main__":
    main()
