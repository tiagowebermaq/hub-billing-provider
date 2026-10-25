"""Topologia RabbitMQ descrita como dados, para ser declarada no boot e testada sem broker."""
from dataclasses import dataclass, field

EX_COLLECT = "hub.collect"
EX_BILLS = "hub.bills"
EX_EVENTS = "hub.events"
EX_RETRY = "hub.retry"
EX_DLX = "hub.dlx"

RETRY_DELAYS_MS = (30_000, 300_000, 3_600_000)  # 30 s, 5 min, 1 h


@dataclass(frozen=True)
class QueueSpec:
    name: str
    exchange: str
    routing_key: str
    arguments: dict[str, object] = field(default_factory=dict)


def _main(name: str, exchange: str, key: str) -> QueueSpec:
    return QueueSpec(name, exchange, key, {"x-dead-letter-exchange": EX_DLX, "x-dead-letter-routing-key": name})


MAIN_QUEUES = (
    _main("collect.request", EX_COLLECT, "collect.request.*"),
    _main("collect.result", EX_COLLECT, "collect.result.*"),
    _main("bill.normalized", EX_BILLS, "bill.normalized"),
    _main("batch.ready", EX_BILLS, "batch.ready"),
    _main("events.webhook", EX_EVENTS, ""),
)

EXCHANGES = {EX_COLLECT: "topic", EX_BILLS: "topic", EX_EVENTS: "fanout", EX_RETRY: "direct", EX_DLX: "direct"}


def retry_queue_name(queue: str, delay_ms: int) -> str:
    return f"{queue}.retry.{delay_ms // 1000}s"


def retry_queues() -> list[QueueSpec]:
    """Uma fila de espera por atraso; ao expirar o TTL a mensagem volta para a fila original."""
    specs = []
    for q in MAIN_QUEUES:
        for delay in RETRY_DELAYS_MS:
            name = retry_queue_name(q.name, delay)
            specs.append(QueueSpec(name, EX_RETRY, name, {
                "x-message-ttl": delay, "x-dead-letter-exchange": "", "x-dead-letter-routing-key": q.name,
            }))
    return specs


def dlq_queues() -> list[QueueSpec]:
    return [QueueSpec(f"{q.name}.dlq", EX_DLX, q.name) for q in MAIN_QUEUES]


def retry_target(queue: str, attempt: int) -> str | None:
    """Fila de retry para a tentativa que falhou, ou None quando deve ir para a DLQ."""
    if attempt > len(RETRY_DELAYS_MS):
        return None
    return retry_queue_name(queue, RETRY_DELAYS_MS[attempt - 1])
