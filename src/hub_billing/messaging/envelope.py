from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from hub_billing.core import correlation


class Envelope(BaseModel):
    """Envelope padrão de todas as mensagens do hub."""

    message_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    correlation_id: str = Field(default_factory=correlation.ensure)
    causation_id: str | None = None
    type: str
    version: int = 1
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    producer: str
    attempt: int = 1
    payload: dict[str, Any] = Field(default_factory=dict)

    def derive(self, type: str, producer: str, payload: dict[str, Any]) -> Envelope:  # noqa: A002
        """Nova mensagem causada por esta: mantém o correlation_id e aponta o causation_id."""
        return Envelope(correlation_id=self.correlation_id, causation_id=self.message_id,
                        type=type, producer=producer, payload=payload)

    def next_attempt(self) -> Envelope:
        return self.model_copy(update={"attempt": self.attempt + 1})
