import json
import logging
import sys
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from hub_billing.core import correlation


class JsonFormatter(logging.Formatter):
    """Logs em JSON, sempre com o Correlation ID do contexto."""

    def format(self, record: logging.LogRecord) -> str:
        data = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "correlation_id": correlation.get(),
        }
        extra = getattr(record, "extra_fields", None)
        if isinstance(extra, dict):
            data.update(extra)
        if record.exc_info:
            data["exc"] = self.formatException(record.exc_info)
        return json.dumps(data, ensure_ascii=False, default=str)


def setup(level: str = "INFO", file: Path | None = None) -> None:
    """Logs no console e, com file, também num arquivo (logs/ na pasta do hub), para ler sem abrir as janelas."""
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if file is not None:
        file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(RotatingFileHandler(file, maxBytes=2_000_000, backupCount=3, encoding="utf-8"))
    for handler in handlers:
        handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = handlers
    root.setLevel(level)
