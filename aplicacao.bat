@echo off
REM Hub Billing Provider - inicia a aplicação: infraestrutura no Docker, API e os 5 workers, cada um na sua janela.
REM Para parar: feche as janelas "Hub ..." e, se quiser, rode "docker compose stop".
setlocal
cd /d "%~dp0"
REM Porta do hub: 8010 (a 8000 costuma estar em uso por outros projetos). Para trocar: set HUB_PORT=8020 antes de rodar.
if "%HUB_PORT%"=="" set HUB_PORT=8010
netstat -ano | findstr /R /C:":%HUB_PORT% .*LISTENING" >nul && (echo [ERRO] A porta %HUB_PORT% ja esta em uso por outro programa. Feche-o ou rode: set HUB_PORT=8020 ^& aplicacao.bat & pause & exit /b 1)

if not exist .venv\Scripts\python.exe (echo Rode iniciar.bat primeiro. & pause & exit /b 1)
if not exist .env (echo Rode iniciar.bat primeiro. & pause & exit /b 1)

docker compose up -d rabbitmq redis postgres || (echo [ERRO] Abra o Docker Desktop e tente de novo. & pause & exit /b 1)
echo Aguardando os servicos ficarem prontos...
timeout /t 10 /nobreak >nul

start "Hub API" cmd /k .venv\Scripts\uvicorn.exe hub_billing.api.app:create_app --factory --host 127.0.0.1 --port %HUB_PORT%
for %%W in (collector normalizer grouper dispatcher webhooks) do start "Hub %%W" /min cmd /k .venv\Scripts\hub-worker.exe %%W

timeout /t 5 /nobreak >nul
start "" http://localhost:%HUB_PORT%/painel
echo.
echo Hub rodando em http://localhost:%HUB_PORT%  (painel em /painel, documentacao da API em /docs)
echo Login assistido nos portais:
echo   .venv\Scripts\python.exe tools\login_portal.py cpfl --distributor rge --hub http://localhost:%HUB_PORT%
echo   .venv\Scripts\python.exe tools\login_portal.py aegea --distributor corsan --hub http://localhost:%HUB_PORT%
pause
