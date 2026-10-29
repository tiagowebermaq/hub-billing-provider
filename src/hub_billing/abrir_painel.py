"""Abre o painel do hub numa janela própria do Chromium instalado pelo iniciar.bat (o navegador do hub).

Assim o painel e as janelas de login dos portais usam sempre o mesmo navegador, separado do Chrome do dia a dia.
O perfil fica em .hub-navegador/ na pasta do hub, para o "Lembrar de mim" funcionar entre aberturas.
Se o Chromium não estiver instalado, cai no navegador padrão do sistema.

    hub-painel http://localhost:8010/painel
"""
from __future__ import annotations

import subprocess
import sys
import webbrowser
from pathlib import Path

PERFIL = Path.cwd() / ".hub-navegador"


def chromium_path() -> str | None:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            path = pw.chromium.executable_path
    except Exception:  # playwright ausente ou navegador não baixado
        return None
    return path if Path(path).exists() else None


def comando(chromium: str, url: str, perfil: Path = PERFIL) -> list[str]:
    return [chromium, f"--app={url}", f"--user-data-dir={perfil}", "--no-first-run",
            "--no-default-browser-check", "--window-size=1366,860"]


def abrir(url: str) -> str:
    chromium = chromium_path()
    if chromium is None:
        webbrowser.open(url)
        return "navegador padrão"
    subprocess.Popen(comando(chromium, url), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     close_fds=True, creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
    return "navegador do hub"


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    url = args[0] if args else "http://localhost:8010/painel"
    print(f"Painel aberto no {abrir(url)}: {url}")


if __name__ == "__main__":
    main()
