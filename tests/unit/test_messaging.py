from hub_billing.core import correlation
from hub_billing.messaging import topology
from hub_billing.messaging.envelope import Envelope


def test_envelope_uses_current_correlation_id() -> None:
    token = correlation.set("cid-abc")
    try:
        env = Envelope(type="collect.request", producer="api")
    finally:
        correlation.reset(token)
    assert env.correlation_id == "cid-abc"


def test_derive_keeps_correlation_and_sets_causation() -> None:
    parent = Envelope(type="collect.result", producer="collector", correlation_id="cid")
    child = parent.derive("bill.normalized", "normalizer", {"x": 1})
    assert child.correlation_id == "cid"
    assert child.causation_id == parent.message_id
    assert child.message_id != parent.message_id
    assert parent.next_attempt().attempt == 2


def test_retry_then_dlq() -> None:
    assert topology.retry_target("bill.normalized", 1) == "bill.normalized.retry.30s"
    assert topology.retry_target("bill.normalized", 3) == "bill.normalized.retry.3600s"
    assert topology.retry_target("bill.normalized", 4) is None


def test_every_main_queue_has_dlq_and_retry_back_to_itself() -> None:
    names = {q.name for q in topology.MAIN_QUEUES}
    assert {q.name for q in topology.dlq_queues()} == {f"{n}.dlq" for n in names}
    for spec in topology.retry_queues():
        assert spec.arguments["x-dead-letter-routing-key"] in names
        assert spec.arguments["x-message-ttl"] in topology.RETRY_DELAYS_MS
    assert all(q.arguments["x-dead-letter-exchange"] == topology.EX_DLX for q in topology.MAIN_QUEUES)
