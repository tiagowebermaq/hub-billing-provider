from typing import Any

import pytest

from hub_billing.api import app as api_app
from hub_billing.messaging import rabbit
from hub_billing.messaging.envelope import Envelope


class FakeExchange:
    def __init__(self, sent: list[tuple[str, str]], name: str) -> None:
        self.sent, self.name = sent, name

    async def publish(self, message: Any, routing_key: str) -> None:
        self.sent.append((self.name, routing_key))


class FakeChannel:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self.is_closed = False
        self.default_exchange = FakeExchange(self.sent, "")

    async def get_exchange(self, name: str) -> FakeExchange:
        return FakeExchange(self.sent, name)


class FakeConnection:
    def __init__(self, channel: FakeChannel) -> None:
        self._channel = channel

    async def channel(self) -> FakeChannel:
        return self._channel


async def test_publica_no_rabbitmq_e_reaproveita_a_conexao(monkeypatch: pytest.MonkeyPatch) -> None:
    channel, conexoes, declarados = FakeChannel(), [], []

    async def connect(url: str) -> FakeConnection:
        conexoes.append(url)
        return FakeConnection(channel)

    async def declare(ch: Any) -> None:
        declarados.append(ch)

    monkeypatch.setattr(rabbit.aio_pika, "connect_robust", connect)
    monkeypatch.setattr(rabbit, "declare", declare)
    pub = rabbit.RabbitPublisher("amqp://hub:hub@localhost:5672/")
    env = Envelope(type="collect.request", producer="api", payload={"provider": "cpfl"})
    await pub("hub.collect", "collect.request.cpfl", env)
    await pub("hub.collect", "collect.request.aegea", env)
    assert channel.sent == [("hub.collect", "collect.request.cpfl"), ("hub.collect", "collect.request.aegea")]
    assert len(conexoes) == 1 and len(declarados) == 1
    channel.is_closed = True  # canal caiu: abre de novo
    await pub("hub.collect", "collect.request.cpfl", env)
    assert len(conexoes) == 2


def test_app_de_producao_usa_o_rabbitmq(monkeypatch: pytest.MonkeyPatch) -> None:
    from hub_billing.core import config

    monkeypatch.setenv("HUB_DATABASE_URL", "sqlite+pysqlite:///:memory:")
    config.get_settings.cache_clear()
    try:
        app = api_app.create_production_app()
        assert isinstance(app.state.publisher, rabbit.RabbitPublisher)
    finally:
        config.get_settings.cache_clear()
