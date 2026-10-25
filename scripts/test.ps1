$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
.\.venv\Scripts\ruff.exe check src tests tools
.\.venv\Scripts\mypy.exe src
.\.venv\Scripts\pytest.exe --cov --cov-report=term-missing --cov-fail-under=80
