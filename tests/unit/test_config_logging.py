import json
import logging
from pathlib import Path

import pytest

from hub_billing.core import config
from hub_billing.core import logging as hub_logging
from hub_billing.core.config import Settings


def test_settings_read_hub_prefixed_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HUB_REDIS_URL", "redis://cache:6379/1")
    monkeypatch.setenv("HUB_JWT_EXPIRE_MINUTES", "5")
    s = Settings(_env_file=None)
    assert s.redis_url == "redis://cache:6379/1"
    assert s.jwt_expire_minutes == 5


def test_get_settings_is_cached() -> None:
    config.get_settings.cache_clear()
    assert config.get_settings() is config.get_settings()
    config.get_settings.cache_clear()


def test_setup_installs_single_json_handler(capsys: pytest.CaptureFixture[str]) -> None:
    root = logging.getLogger()
    old_handlers, old_level = root.handlers[:], root.level
    try:
        hub_logging.setup("DEBUG")
        hub_logging.setup("DEBUG")
        assert len(root.handlers) == 1
        try:
            raise RuntimeError("falhou")
        except RuntimeError:
            logging.getLogger("teste").exception("erro com acento: coleta")
        line = capsys.readouterr().out.strip().splitlines()[-1]
        data = json.loads(line)
        assert data["level"] == "ERROR"
        assert data["msg"] == "erro com acento: coleta"
        assert "RuntimeError: falhou" in data["exc"]
    finally:
        root.handlers[:] = old_handlers
        root.setLevel(old_level)


def test_setup_grava_tambem_em_arquivo(tmp_path: Path) -> None:
    root = logging.getLogger()
    old_handlers, old_level = root.handlers[:], root.level
    try:
        hub_logging.setup("INFO", tmp_path / "logs" / "worker-collector.log")
        logging.getLogger("hub.worker").info("coleta concluída")
        for h in root.handlers:
            h.flush()
        line = (tmp_path / "logs" / "worker-collector.log").read_text(encoding="utf-8").strip()
        assert json.loads(line)["msg"] == "coleta concluída"
    finally:
        for h in root.handlers:
            if h not in old_handlers:
                h.close()
        root.handlers[:] = old_handlers
        root.setLevel(old_level)
