import json
import logging

from hub_billing.core import correlation
from hub_billing.core.logging import JsonFormatter


def test_ensure_creates_and_keeps_id() -> None:
    token = correlation.set(None)
    try:
        first = correlation.ensure()
        assert first == correlation.ensure()
    finally:
        correlation.reset(token)


def test_json_log_includes_correlation_id() -> None:
    token = correlation.set("cid-123")
    try:
        record = logging.LogRecord("t", logging.INFO, __file__, 1, "olá %s", ("mundo",), None)
        record.extra_fields = {"worker": "normalizer"}
        data = json.loads(JsonFormatter().format(record))
    finally:
        correlation.reset(token)
    assert data["correlation_id"] == "cid-123"
    assert data["msg"] == "olá mundo"
    assert data["worker"] == "normalizer"
