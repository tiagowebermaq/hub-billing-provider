#!/usr/bin/env sh
# Hub Billing Provider - preparação no Ubuntu (rode uma vez, ou depois de atualizar o código).
set -e
cd "$(dirname "$0")"
command -v python3 >/dev/null || { echo "Python 3.12+ não encontrado"; exit 1; }
command -v docker >/dev/null || { echo "Docker não encontrado"; exit 1; }
echo "== 1/5 Ambiente Python"; [ -x .venv/bin/python ] || python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip -q
echo "== 2/5 Dependências"; .venv/bin/pip install -e ".[dev,portal]" -q
.venv/bin/python -m playwright install chromium
echo "== 3/5 Configuração (.env)"; .venv/bin/python tools/setup_env.py
echo "== 4/5 RabbitMQ, Redis e PostgreSQL"; docker compose up -d rabbitmq redis postgres
echo "== 5/5 Testes"; .venv/bin/pytest -q
echo "Pronto. Revise o .env (HUB_ADMIN_EMAIL, HUB_ADMIN_PASSWORD, HUB_AEGEA_APIM_KEY) e rode ./aplicacao.sh"
