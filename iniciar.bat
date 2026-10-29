@echo off
REM Hub Billing Provider - preparação (rode uma vez, ou depois de atualizar o código).
REM Cria o ambiente Python, instala tudo, gera o .env com segredos e sobe RabbitMQ, Redis e PostgreSQL no Docker.
setlocal
cd /d "%~dp0"

where python >nul 2>nul || (echo [ERRO] Python 3.12+ nao encontrado. Instale em https://www.python.org/downloads/ & goto :fim)
where docker >nul 2>nul || (echo [ERRO] Docker Desktop nao encontrado. Instale e abra o Docker Desktop. & goto :fim)

tasklist /FI "IMAGENAME eq hub-worker.exe" 2>nul | find /I "hub-worker.exe" >nul && (echo [ERRO] O hub esta rodando. Rode parar.bat e depois iniciar.bat de novo. & goto :fim)

echo == 1/5 Ambiente Python
if not exist .venv\Scripts\python.exe python -m venv .venv || goto :erro
REM Remove sobras de instalacoes interrompidas (pastas "~..."), que geram avisos "Ignoring invalid distribution".
if exist .venv\Lib\site-packages for /d %%D in (".venv\Lib\site-packages\~*") do rmdir /s /q "%%D"
.venv\Scripts\python.exe -m pip install --upgrade pip -q || goto :erro

echo == 2/5 Dependencias
.venv\Scripts\pip.exe install -e ".[dev,portal]" -q || goto :erro
.venv\Scripts\python.exe -m playwright install chromium || goto :erro

echo == 3/5 Configuracao (.env)
.venv\Scripts\python.exe tools\setup_env.py || goto :erro

echo == 4/5 RabbitMQ, Redis e PostgreSQL
docker compose up -d rabbitmq redis postgres || goto :erro

echo == 5/5 Testes
.venv\Scripts\pytest.exe -q || goto :erro

echo.
echo Pronto. Revise o .env (HUB_ADMIN_EMAIL, HUB_ADMIN_PASSWORD, HUB_AEGEA_APIM_KEY) e rode aplicacao.bat.
goto :fim

:erro
echo.
echo [ERRO] A preparacao parou no passo acima.
:fim
pause
