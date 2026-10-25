#!/usr/bin/env sh
# Ubuntu: roda lint, tipos e testes unitários dentro do container.
set -e
cd "$(dirname "$0")/.."
docker compose --profile test run --rm --build tests
