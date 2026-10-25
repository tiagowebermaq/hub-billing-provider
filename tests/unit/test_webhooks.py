import json

import httpx
import pytest
import respx

from hub_billing.messaging.envelope import Envelope
from hub_billing.webhooks.dispatcher import DeliveryError, Subscription, deliver
from hub_billing.webhooks.signing import sign, verify

SUB = Subscription(id="s1", url="https://cliente.example/hook", secret="seg", events=frozenset({"bill.collected"}))


def test_sign_and_verify() -> None:
    sig = sign("seg", 1000, b"{}")
    assert verify("seg", 1000, b"{}", sig, now=1100)
    assert not verify("outro", 1000, b"{}", sig, now=1100)
    assert not verify("seg", 1000, b"{}", sig, now=5000)  # replay fora da janela


def test_subscription_filter() -> None:
    assert SUB.wants("bill.collected") and not SUB.wants("batch.sent")
    assert Subscription("s2", "u", "k", frozenset({"*"})).wants("batch.sent")


@respx.mock
async def test_deliver_sends_signed_request() -> None:
    route = respx.post(SUB.url).mock(return_value=httpx.Response(200))
    env = Envelope(type="bill.collected", producer="normalizer", correlation_id="cid-7", payload={"id": "b1"})
    async with httpx.AsyncClient() as client:
        assert await deliver(client, SUB, env) == 200
    req = route.calls.last.request
    assert req.headers["X-Correlation-ID"] == "cid-7"
    assert req.headers["X-Hub-Delivery"] == env.message_id
    ts = int(req.headers["X-Hub-Timestamp"])
    assert verify("seg", ts, req.content, req.headers["X-Hub-Signature-256"], now=ts)
    assert json.loads(req.content)["data"] == {"id": "b1"}


@respx.mock
@pytest.mark.parametrize("response", [httpx.Response(500), httpx.ConnectError("down")])
async def test_deliver_failure_raises(response: object) -> None:
    route = respx.post(SUB.url)
    if isinstance(response, Exception):
        route.mock(side_effect=response)
    else:
        route.mock(return_value=response)
    async with httpx.AsyncClient() as client:
        with pytest.raises(DeliveryError):
            await deliver(client, SUB, Envelope(type="bill.collected", producer="t"))
