"""Ponto de entrada dos workers: python -m hub_billing.workers.run <collector|normalizer|grouper|...>."""
import argparse
import asyncio
import logging

import aio_pika

from hub_billing.adapters.registry import load_builtin
from hub_billing.core import logging as hub_logging
from hub_billing.core.config import get_settings
from hub_billing.messaging import rabbit
from hub_billing.messaging.envelope import Envelope

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

    async def handler(env: Envelope) -> None:
        # Etapas concretas entram nos épicos E4-E8; por ora o worker registra o que recebe.
        log.info("recebido %s", env.type, extra={"extra_fields": {"worker": name, "message_id": env.message_id}})

    await rabbit.consume(channel, WORKERS[name], handler)
    log.info("worker %s ouvindo %s", name, WORKERS[name])
    await asyncio.Future()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("worker", choices=sorted(WORKERS))
    asyncio.run(_run(parser.parse_args().worker))


if __name__ == "__main__":
    main()
