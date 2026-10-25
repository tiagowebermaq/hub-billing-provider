# Windows: roda lint, tipos e testes unitários dentro do container (Docker Desktop).
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
docker compose --profile test run --rm --build tests
