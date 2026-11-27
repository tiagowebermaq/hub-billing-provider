#!/usr/bin/env bash
# Hub Billing Provider - para o hub no servidor. Os dados ficam salvos; para subir de novo: ./publicar.sh
set -euo pipefail
cd "$(dirname "$0")"
docker compose -f docker-compose.yml -f deploy/docker-compose.servidor.yml --profile caddy stop
