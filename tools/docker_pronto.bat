@echo off
REM Garante que o Docker Desktop esta aberto e pronto. Se estiver fechado (ex.: depois de ligar o PC), abre e espera
REM ate 3 minutos. Chamado por iniciar.bat e aplicacao.bat: call tools\docker_pronto.bat
docker info >nul 2>nul && exit /b 0
set "DOCKER_DESKTOP=%ProgramFiles%\Docker\Docker\Docker Desktop.exe"
if not exist "%DOCKER_DESKTOP%" set "DOCKER_DESKTOP=%LOCALAPPDATA%\Programs\Docker\Docker\Docker Desktop.exe"
if not exist "%DOCKER_DESKTOP%" (echo [ERRO] Docker Desktop nao encontrado. Abra o Docker Desktop e tente de novo. & exit /b 1)
echo Docker Desktop fechado: abrindo e esperando ficar pronto (ate 3 minutos)...
start "" "%DOCKER_DESKTOP%"
for /l %%I in (1,1,36) do (
    timeout /t 5 /nobreak >nul
    docker info >nul 2>nul && (echo Docker pronto. & exit /b 0)
)
echo [ERRO] O Docker Desktop nao ficou pronto em 3 minutos. Confira a janela dele e tente de novo.
exit /b 1
