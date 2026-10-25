# Gera nova versão do PO. Exemplo: .\scripts\po.ps1 --bump minor -m "Adiciona provedor X" --git
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
.\.venv\Scripts\python.exe tools\build_po.py @args
