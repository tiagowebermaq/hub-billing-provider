from pathlib import Path
from typing import Any

import pytest

from hub_billing import abrir_painel


def test_comando_abre_janela_de_app_com_perfil_proprio(tmp_path: Path) -> None:
    cmd = abrir_painel.comando("chromium.exe", "http://localhost:8010/painel", tmp_path / "perfil")
    assert cmd[0] == "chromium.exe"
    assert "--app=http://localhost:8010/painel" in cmd
    assert f"--user-data-dir={tmp_path / 'perfil'}" in cmd


def test_usa_navegador_do_hub_e_cai_no_padrao(monkeypatch: pytest.MonkeyPatch,
                                               capsys: pytest.CaptureFixture[str]) -> None:
    abertos: list[Any] = []
    monkeypatch.setattr(abrir_painel.subprocess, "Popen", lambda cmd, **kw: abertos.append(cmd))
    monkeypatch.setattr(abrir_painel.webbrowser, "open", lambda url: abertos.append(url))

    monkeypatch.setattr(abrir_painel, "chromium_path", lambda: "/opt/chromium")
    abrir_painel.main(["http://localhost:8010/painel"])
    assert abertos[-1][0] == "/opt/chromium" and "navegador do hub" in capsys.readouterr().out

    monkeypatch.setattr(abrir_painel, "chromium_path", lambda: None)
    abrir_painel.main([])
    assert abertos[-1] == "http://localhost:8010/painel" and "navegador padrão" in capsys.readouterr().out


def test_chromium_path_sem_playwright(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins

    original = builtins.__import__

    def sem_playwright(name: str, *args: Any, **kwargs: Any) -> Any:
        if name.startswith("playwright"):
            raise ImportError(name)
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", sem_playwright)
    assert abrir_painel.chromium_path() is None


def test_chromium_path_com_playwright() -> None:
    caminho = abrir_painel.chromium_path()
    assert caminho is None or Path(caminho).exists()
