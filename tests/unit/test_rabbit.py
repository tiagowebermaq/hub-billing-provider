"""Testa a integração aio-pika com um canal simulado, sem precisar de RabbitMQ rodando."""
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from hub_billing.core import correlation
from hub_billing.messaging import rabbit, topology
from hub_billing.messaging.envelope import Envelope


def fake_channel() -> MagicMock:
    channel = MagicMock()
    channel.declare_exchange = AsyncMock()
    queue = MagicMock(bind=AsyncMock(), consume=AsyncMock())
    channel.declare_queue = AsyncMock(return_value=queue)
    exchange = MagicMock(publish=AsyncMock())
    channel.get_exchange = AsyncMock(return_value=exchange)
    channel.default_exchange = MagicMock(publish=AsyncMock())
    channel.get_queue = AsyncMock(return_value=queue)
    channel.set_qos = AsyncMock()
    channel.queue, channel.exchange = queue, exchange
    return channel


async def test_declare_creates_full_topology() -> None:
    ch = fake_channel()
    await rabbit.declare(ch)
    assert ch.declare_exchange.await_count == len(topology.EXCHANGES)
    expected = len(topology.MAIN_QUEUES) * (1 + len(topology.RETRY_DELAYS_MS) + 1)
    assert ch.declare_queue.await_count == expected
    names = {c.args[0] for c in ch.declare_queue.await_args_list}
    assert {"bill.normalized", "bill.normalized.dlq", "bill.normalized.retry.30s"} <= names


async def test_publish_sets_persistent_message_with_correlation_id() -> None:
    ch = fake_channel()
    env = Envelope(type="collect.request", producer="api", correlation_id="cid-9")
    await rabbit.publish(ch, topology.EX_COLLECT, "collect.request.cpfl", env)
    msg = ch.exchange.publish.await_args.args[0]
    assert msg.correlation_id == "cid-9"
    assert msg.message_id == env.message_id
    assert msg.delivery_mode.value == 2
    assert Envelope.model_validate_json(msg.body).type == "collect.request"
    assert ch.exchange.publish.await_args.kwargs["routing_key"] == "collect.request.cpfl"


async def test_publish_to_default_exchange() -> None:
    ch = fake_channel()
    await rabbit.publish(ch, "", "fila", Envelope(type="t", producer="p"))
    ch.default_exchange.publish.assert_awaited_once()


def incoming(env: Envelope) -> MagicMock:
    return MagicMock(body=env.model_dump_json().encode(), ack=AsyncMock(), reject=AsyncMock())


async def start_consumer(handler: Any) -> tuple[MagicMock, Any]:
    ch = fake_channel()
    await rabbit.consume(ch, "bill.normalized", handler)
    return ch, ch.queue.consume.await_args.args[0]


async def test_consume_acks_and_sets_correlation_context() -> None:
    seen: list[str | None] = []

    async def handler(env: Envelope) -> None:
        seen.append(correlation.get())

    _, on_message = await start_consumer(handler)
    msg = incoming(Envelope(type="bill.normalized", producer="t", correlation_id="cid-ctx"))
    await on_message(msg)
    assert seen == ["cid-ctx"]
    msg.ack.assert_awaited_once()
    assert correlation.get() != "cid-ctx"


@pytest.mark.parametrize(("attempt", "retry_queue"), [(1, "bill.normalized.retry.30s"), (2, "bill.normalized.retry.300s")])
async def test_consume_failure_goes_to_retry(attempt: int, retry_queue: str) -> None:
    async def handler(env: Envelope) -> None:
        raise RuntimeError("boom")

    ch, on_message = await start_consumer(handler)
    msg = incoming(Envelope(type="bill.normalized", producer="t", attempt=attempt))
    await on_message(msg)
    ch.get_exchange.assert_awaited_with(topology.EX_RETRY)
    published = ch.exchange.publish.await_args
    assert published.kwargs["routing_key"] == retry_queue
    assert Envelope.model_validate_json(published.args[0].body).attempt == attempt + 1
    msg.ack.assert_awaited_once()


async def test_consume_last_failure_goes_to_dlq() -> None:
    async def handler(env: Envelope) -> None:
        raise RuntimeError("boom")

    ch, on_message = await start_consumer(handler)
    msg = incoming(Envelope(type="bill.normalized", producer="t", attempt=len(topology.RETRY_DELAYS_MS) + 1))
    await on_message(msg)
    msg.reject.assert_awaited_once_with(requeue=False)
    msg.ack.assert_not_awaited()
