"""Integração aio-pika: declara topologia, publica e consome com ack manual, retry e DLQ."""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

import aio_pika
from aio_pika.abc import AbstractChannel, AbstractIncomingMessage

from hub_billing.core import correlation
from hub_billing.messaging import topology
from hub_billing.messaging.envelope import Envelope

log = logging.getLogger(__name__)
Handler = Callable[[Envelope], Awaitable[None]]


async def declare(channel: AbstractChannel) -> None:
    for name, kind in topology.EXCHANGES.items():
        await channel.declare_exchange(name, aio_pika.ExchangeType(kind), durable=True)
    for spec in [*topology.MAIN_QUEUES, *topology.retry_queues(), *topology.dlq_queues()]:
        queue = await channel.declare_queue(spec.name, durable=True, arguments=dict(spec.arguments))  # type: ignore[arg-type]
        await queue.bind(spec.exchange, routing_key=spec.routing_key)


async def publish(channel: AbstractChannel, exchange: str, routing_key: str, env: Envelope) -> None:
    ex = await channel.get_exchange(exchange) if exchange else channel.default_exchange
    await ex.publish(
        aio_pika.Message(
            body=env.model_dump_json().encode(),
            content_type="application/json",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            message_id=env.message_id,
            correlation_id=env.correlation_id,
            type=env.type,
        ),
        routing_key=routing_key,
    )


async def consume(channel: AbstractChannel, queue_name: str, handler: Handler, prefetch: int = 10) -> None:
    await channel.set_qos(prefetch_count=prefetch)
    queue = await channel.get_queue(queue_name)

    async def on_message(msg: AbstractIncomingMessage) -> None:
        env = Envelope.model_validate_json(msg.body)
        token = correlation.set(env.correlation_id)
        try:
            await handler(env)
            await msg.ack()
        except Exception:
            log.exception("falha ao processar %s (tentativa %s)", env.type, env.attempt)
            target = topology.retry_target(queue_name, env.attempt)
            if target is None:
                await msg.reject(requeue=False)  # dead-letter -> DLQ
            else:
                await publish(channel, topology.EX_RETRY, target, env.next_attempt())
                await msg.ack()
        finally:
            correlation.reset(token)

    await queue.consume(on_message)
