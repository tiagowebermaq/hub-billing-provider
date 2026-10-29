#!/usr/bin/env sh
# Hub Billing Provider - inicia infraestrutura, API e os 5 workers no Ubuntu. Ctrl+C para tudo.
set -e
cd "$(dirname "$0")"
[ -x .venv/bin/python ] && [ -f .env ] || { echo "Rode ./iniciar.sh primeiro"; exit 1; }
docker compose up -d rabbitmq redis postgres
echo "Aguardando os serviços ficarem prontos..."; sleep 10
mkdir -p logs
for w in collector normalizer grouper dispatcher webhooks; do
  .venv/bin/hub-worker "$w" > "logs/worker-$w.log" 2>&1 &
done
trap 'kill $(jobs -p) 2>/dev/null' EXIT INT TERM
echo "Hub rodando em http://localhost:8000 (documentação em /docs). Logs dos workers em logs/."
.venv/bin/uvicorn hub_billing.api.app:create_app --factory --host 127.0.0.1 --port 8000
