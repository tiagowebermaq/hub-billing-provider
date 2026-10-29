#!/usr/bin/env sh
# Hub Billing Provider - para os workers e a API que ficaram rodando e para RabbitMQ, Redis e PostgreSQL no Docker.
cd "$(dirname "$0")"
PORT="${HUB_PORT:-8010}"
pkill -f "\.venv/bin/hub-worker" 2>/dev/null
pkill -f "uvicorn hub_billing.api.app:.*--port $PORT" 2>/dev/null
docker compose stop
echo "Hub parado. Para iniciar de novo: ./aplicacao.sh"
