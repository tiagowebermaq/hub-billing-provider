@echo off
REM Hub Billing Provider - para a aplicação: fecha a API e os 5 workers e para RabbitMQ, Redis e PostgreSQL no Docker.
REM Os dados ficam salvos; para subir de novo, rode aplicacao.bat.
setlocal
cd /d "%~dp0"
if "%HUB_PORT%"=="" set HUB_PORT=8010

echo == Fechando as janelas do hub (API e workers)
taskkill /FI "WINDOWTITLE eq Hub API*" /FI "IMAGENAME eq cmd.exe" /T /F >nul 2>nul
for %%W in (collector normalizer grouper dispatcher webhooks) do taskkill /FI "WINDOWTITLE eq Hub %%W*" /FI "IMAGENAME eq cmd.exe" /T /F >nul 2>nul
taskkill /IM hub-worker.exe /F >nul 2>nul
REM Se a API ainda estiver na porta do hub, encerra só ela (outros projetos em outras portas não são tocados).
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":%HUB_PORT% .*LISTENING"') do taskkill /PID %%P /T /F >nul 2>nul

echo == Parando RabbitMQ, Redis e PostgreSQL
where docker >nul 2>nul && docker compose stop

echo.
echo Hub parado. Para iniciar de novo: aplicacao.bat
pause
