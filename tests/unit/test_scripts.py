from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_scripts_windows_usam_crlf() -> None:
    for name in ("iniciar.bat", "aplicacao.bat", "parar.bat", "resetar-senha.bat"):
        data = (ROOT / name).read_bytes()
        assert data.count(b"\n") == data.count(b"\r\n"), f"{name} precisa de quebras de linha CRLF"


def test_parar_fecha_workers_e_api_do_hub() -> None:
    bat = (ROOT / "parar.bat").read_text(encoding="utf-8")
    assert "hub-worker.exe" in bat and "Hub API" in bat and "docker compose stop" in bat
    assert "uvicorn.exe" not in bat  # não derruba a API de outros projetos que usam uvicorn
    assert "parar.bat" in (ROOT / "iniciar.bat").read_text(encoding="utf-8")
