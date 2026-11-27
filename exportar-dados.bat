@echo off
REM Hub Billing Provider - gera a pasta "migracao" com os dados do PC para levar ao servidor (VM do omnichannel).
REM Leva: o banco (faturas, contas, usuarios, sessoes dos portais) e o .env (as chaves que abrem as senhas cifradas).
REM Copie a pasta migracao inteira para a pasta do hub no servidor e rode ./publicar.sh la.
setlocal
cd /d "%~dp0"
if not exist .env (echo Nao achei o .env: rode este arquivo na pasta do hub. & pause & exit /b 1)
call tools\docker_pronto.bat || (pause & exit /b 1)
docker compose up -d postgres || (echo [ERRO] Abra o Docker Desktop e tente de novo. & pause & exit /b 1)
timeout /t 5 /nobreak >nul
if not exist migracao mkdir migracao
echo == Exportando o banco
docker compose exec -T postgres pg_dump -U hub -d hub --no-owner --no-privileges > migracao\hub-dados.sql || (echo [ERRO] Nao consegui exportar o banco. & pause & exit /b 1)
copy /y .env migracao\env-do-pc.txt >nul
echo.
echo Pronto: pasta migracao criada. Ela tem senhas: leve ao servidor e depois apague a copia.
pause
