$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
.\.venv\Scripts\uvicorn.exe hub_billing.api.app:create_app --factory --reload --port 8010
