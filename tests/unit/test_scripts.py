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


def test_painel_abre_no_navegador_padrao() -> None:
    bat = (ROOT / "aplicacao.bat").read_text(encoding="utf-8")
    assert 'start "" http://localhost:%HUB_PORT%/painel' in bat and "abrir_painel" not in bat


def test_api_sobe_publicando_no_rabbitmq() -> None:
    assert "hub_billing.api.app:create_production_app" in (ROOT / "aplicacao.bat").read_text(encoding="utf-8")
    for script in ("aplicacao.sh", "docker/Dockerfile"):  # fora da imagem de testes do Docker
        if (ROOT / script).exists():
            assert "hub_billing.api.app:create_production_app" in (ROOT / script).read_text(encoding="utf-8")
