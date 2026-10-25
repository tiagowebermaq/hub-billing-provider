"""Correlation ID propagado por contextvars em API, workers, logs, mensagens e webhooks."""
import uuid
from contextvars import ContextVar, Token

HEADER = "X-Correlation-ID"
_current: ContextVar[str | None] = ContextVar("correlation_id", default=None)


def new_id() -> str:
    return str(uuid.uuid4())


def get() -> str | None:
    return _current.get()


def ensure() -> str:
    """Retorna o Correlation ID atual, criando um se o fluxo ainda não tiver."""
    cid = _current.get()
    if cid is None:
        cid = new_id()
        _current.set(cid)
    return cid


def set(cid: str | None) -> Token[str | None]:  # noqa: A001 - API intencional: correlation.set(...)
    return _current.set(cid)


def reset(token: Token[str | None]) -> None:
    _current.reset(token)
