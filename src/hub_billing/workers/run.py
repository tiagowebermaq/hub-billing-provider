"""Ponto de entrada dos workers: python -m hub_billing.workers.run <collector|normalizer|grouper|...>."""
import argparse
import asyncio
import logging

import aio_pika
import httpx
from redis.asyncio import Redis

from hub_billing.adapters.registry import load_builtin
from hub_billing.core import logging as hub_logging
from hub_billing.core.config import get_settings
from hub_billing.infra.idempotency import IdempotencyGuard
from hub_billing.infra.repository import Repository
from hub_billing.messaging import rabbit, topology
from hub_billing.messaging.envelope import Envelope
from hub_billing.rental.client import RentalClient
from hub_billing.workers import handlers

WORKERS = {"collector": "collect.request", "normalizer": "collect.result", "grouper": "bill.normalized",
           "dispatcher": "batch.ready", "webhooks": "events.webhook"}
log = logging.getLogger("hub.worker")


async def _run(name: str) -> None:
    settings = get_settings()
    hub_logging.setup(settings.log_level)
    load_builtin()
    connection = await aio_pika.connect_robust(settings.rabbitmq_url)
    channel = await connection.channel()
    await rabbit.declare(channel)
    repo = Repository.from_url(settings.database_url, settings.encryption_key)
    repo.create_schema()
    guard = IdempotencyGuard(Redis.from_url(settings.redis_url), settings.idempotency_ttl_seconds)
    http = httpx.AsyncClient(timeout=30)
    rental = RentalClient(http, settings.rental_api_url, settings.rental_api_secret,
                          settings.rental_timeout_seconds)

    async def emit(out: Envelope | None, exchange: str, key: str) -> None:
        if out is not None:
            await rabbit.publish(channel, exchange, key, out)

    async def handler(env: Envelope) -> None:
        extra = {"extra_fields": {"worker": name, "message_id": env.message_id}}
        if name == "collector":
            options = {"apim_key": settings.aegea_apim_key} if env.payload.get("provider") == "aegea" else {}
            for out in await handlers.collect(env, repo, http, options):
                if out.type == "collect.result":
                    await emit(out, topology.EX_COLLECT, f"collect.result.{env.payload['provider']}")
                else:
                    await emit(out, topology.EX_EVENTS, "")
        elif name == "normalizer":
            await emit(await handlers.normalize(env, guard, repo.find_link), topology.EX_BILLS, "bill.normalized")
        elif name == "grouper":
            await emit(handlers.store_bill(env, repo), topology.EX_EVENTS, "")
        elif name == "dispatcher":
            await emit(await handlers.dispatch(env, repo, rental), topology.EX_EVENTS, "")
        else:
            # webhooks: entrega aos assinantes entra com o cadastro de assinaturas.
            log.info("recebido %s", env.type, extra=extra)

    await rabbit.consume(channel, WORKERS[name], handler)
    log.info("worker %s ouvindo %s", name, WORKERS[name])
    await asyncio.Future()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("worker", choices=sorted(WORKERS))
    asyncio.run(_run(parser.parse_args().worker))


if __name__ == "__main__":
    main()
