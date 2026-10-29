from datetime import date
from decimal import Decimal

import fakeredis.aioredis
import pytest

from hub_billing.adapters.base import RawBill
from hub_billing.adapters.registry import load_builtin
from hub_billing.domain.models import Bill, ConsumerInfo, PropertyInfo
from hub_billing.infra.idempotency import IdempotencyGuard
from hub_billing.infra.repository import AccountLink
from hub_billing.messaging.envelope import Envelope
from hub_billing.workers.handlers import build_batches, normalize

load_builtin()


@pytest.fixture
def guard() -> IdempotencyGuard:
    return IdempotencyGuard(fakeredis.aioredis.FakeRedis(), ttl_seconds=60)


def result_env(account: str = "uc1") -> Envelope:
    raw = RawBill(provider="cpfl", account_number=account, reference_month="2026-10",
                  due_date=date(2026, 10, 20), amount=Decimal("150.00"))
    return Envelope(type="collect.result", producer="collector", correlation_id="cid-1",
                    payload={"raw": raw.model_dump(mode="json"), "distributor": "rge"})


async def test_idempotency_guard(guard: IdempotencyGuard) -> None:
    assert await guard.first_time("k")
    assert not await guard.first_time("k")
    await guard.release("k")
    assert await guard.first_time("k")


async def test_normalize_links_property_and_deduplicates(guard: IdempotencyGuard) -> None:
    def lookup(provider: str, account: str) -> AccountLink:
        return AccountLink("imovel-9", "ctr-9")

    out = await normalize(result_env(), guard, lookup)
    assert out is not None and out.type == "bill.normalized"
    assert out.correlation_id == "cid-1"
    bill = Bill.model_validate(out.payload["bill"])
    assert (bill.property_id, bill.contract_id, bill.distributor) == ("imovel-9", "ctr-9", "rge")
    assert await normalize(result_env(), guard, lookup) is None


async def test_build_batches_emits_one_message_per_property(guard: IdempotencyGuard) -> None:
    bills = []
    for acc, prop in [("uc1", "a"), ("uc2", "a"), ("uc3", "b")]:
        env = await normalize(result_env(acc), guard, lambda p, a, prop=prop: AccountLink(prop, None))
        assert env is not None
        bills.append(Bill.model_validate(env.payload["bill"]))
    out = build_batches(Envelope(type="tick", producer="grouper", correlation_id="cid-1"), bills)
    assert [e.payload["total"] for e in out] == ["300.00", "150.00"]
    assert all(e.type == "batch.ready" and e.correlation_id == "cid-1" for e in out)


async def test_normalize_completa_cadastro_com_o_do_hub_sem_sobrescrever_a_fatura(guard: IdempotencyGuard) -> None:
    env = result_env()
    env.payload["raw"]["payload"] = {"consumer": {"name": "Ana Souza"}, "property": {"zip_code": "90010-000"}}
    link = AccountLink("imovel-9", None, PropertyInfo(address="Rua A, 10", zip_code="00000-000"),
                       ConsumerInfo(name="Nome Antigo", document="123.456.789-00"))
    out = await normalize(env, guard, lambda p, a: link)
    assert out is not None
    bill = Bill.model_validate(out.payload["bill"])
    assert bill.consumer == ConsumerInfo(name="Ana Souza", document="123.456.789-00")
    assert bill.property_info == PropertyInfo(address="Rua A, 10", zip_code="90010-000")
