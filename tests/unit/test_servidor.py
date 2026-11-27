"""Publicação no servidor (VM do omnichannel, https://billing.heimber.com.br)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "deploy" / "docker-compose.servidor.yml"


def test_servidor_fecha_banco_rabbitmq_e_redis() -> None:
    if not COMPOSE.exists():  # a imagem de testes do Docker não leva a pasta deploy
        return
    text = COMPOSE.read_text(encoding="utf-8")
    for service in ("rabbitmq:", "redis:", "postgres:"):
        assert "ports: !reset []" in text.split(f"\n  {service}", 1)[1][:200], service
    assert '127.0.0.1:${HUB_PORT:-8010}:8000' in text  # a API não fica aberta para a internet, só para o proxy
    assert "--proxy-headers" in text and "hub_billing.api.app:create_production_app" in text
    for senha in ("HUB_DB_PASSWORD", "HUB_RABBITMQ_PASSWORD", "HUB_REDIS_PASSWORD"):
        assert senha in text and senha in (ROOT / "publicar.sh").read_text(encoding="utf-8")


def test_servidor_responde_no_dominio() -> None:
    if not COMPOSE.exists():
        return
    assert "billing.heimber.com.br" in (ROOT / "publicar.sh").read_text(encoding="utf-8")
    assert "server_name billing.heimber.com.br" in (ROOT / "deploy" / "nginx-billing.conf").read_text(encoding="utf-8")
    assert "{$HUB_DOMINIO}" in (ROOT / "deploy" / "Caddyfile").read_text(encoding="utf-8")


def test_exportar_dados_leva_banco_e_chaves() -> None:
    bat = (ROOT / "exportar-dados.bat").read_bytes()
    assert bat.count(b"\n") == bat.count(b"\r\n")
    text = bat.decode("utf-8")
    assert "pg_dump" in text and "migracao\\hub-dados.sql" in text and "migracao\\env-do-pc.txt" in text
    if (ROOT / ".gitignore").exists():  # a pasta migracao tem senhas: nunca vai para o GitHub
        assert "migracao/" in (ROOT / ".gitignore").read_text(encoding="utf-8")
