@echo off
REM Hub Billing Provider - volta o admin para o e-mail e a senha do .env (HUB_ADMIN_EMAIL / HUB_ADMIN_PASSWORD).
REM Use quando esquecer a senha do painel. O hub pode estar aberto; o PostgreSQL precisa estar rodando.
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (echo Rode iniciar.bat primeiro. & pause & exit /b 1)
where docker >nul 2>nul && docker compose up -d postgres >nul
.venv\Scripts\python.exe tools\reset_admin.py || (echo [ERRO] Nao foi possivel redefinir. Confira se o Docker Desktop esta aberto. & pause & exit /b 1)
pause
