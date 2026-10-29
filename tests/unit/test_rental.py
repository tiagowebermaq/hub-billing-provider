from datetime import date
from decimal import Decimal

import httpx
import pytest
import respx

from hub_billing.domain.grouping import group_bills
from hub_billing.domain.models import BatchStatus, Bill, BillStatus, UtilityType
from hub_billing.infra.repository import Repository
from hub_billing.messaging.envelope import Envelope
from hub_billing.rental.client import RentalClient, RentalRejected, RentalUnavailable, to_payload
from hub_billing.webhooks.signing import SIGNATURE_HEADER, TIMESTAMP_HEADER, verify
from hub_billing.workers.handlers import dispatch, store_bill

URL = "https://locacao.test/api/bills"


def bill(account: str, prop: str | None = "ap-1") -> Bill:
    return Bill(provider="vivo", utility_type=UtilityType.TELECOM, account_number=account, property_id=prop,
                reference_month="2026-10", due_date=date(2026, 11, 15), amount=Decimal("99.90"), digitable_line="846")


@pytest.fixture
def repo() -> Repository:
    r = Repository.from_url("sqlite+pysqlite:///:memory:")
    r.create_schema()
    return r


def saved_batch(repo: Repository) -> str:
    repo.save_bill(bill("c1"))
    [batch] = group_bills(repo.list_bills(status=BillStatus.NORMALIZED), "cid-9")
    repo.save_batch(batch)
    return batch.id


def test_payload_do_lote() -> None:
    [batch] = group_bills([bill("c1"), bill("c2")])
    data = to_payload(batch)
    assert data["total"] == "199.80" and data["bills"][0]["utility_type"] == "TELECOM"
    assert data["bills"][0]["consumption"] is None


@respx.mock
async def test_envio_assinado_com_idempotencia() -> None:
    [batch] = group_bills([bill("c1")])
    route = respx.post(URL).mock(return_value=httpx.Response(201, json={"status": "accepted"}))
    async with httpx.AsyncClient() as http:
        ack = await RentalClient(http, URL, secret="s3gredo").send(batch, "cid-1")
    req = route.calls.last.request
    assert ack == {"status": "accepted"}
    assert req.headers["Idempotency-Key"] == batch.id and req.headers["X-Correlation-ID"] == "cid-1"
    assert verify("s3gredo", int(req.headers[TIMESTAMP_HEADER]), req.content, req.headers[SIGNATURE_HEADER])


@respx.mock
@pytest.mark.parametrize(("response", "error"), [
    (httpx.Response(503), RentalUnavailable), (httpx.Response(429), RentalUnavailable),
    (httpx.ConnectError("x"), RentalUnavailable), (httpx.Response(422, text="contrato inexistente"), RentalRejected),
])
async def test_erros_temporarios_e_definitivos(response: object, error: type[Exception]) -> None:
    [batch] = group_bills([bill("c1")])
    respx.post(URL).mock(side_effect=[response] if isinstance(response, Exception) else None,
                         return_value=None if isinstance(response, Exception) else response)
    async with httpx.AsyncClient() as http:
        with pytest.raises(error):
            await RentalClient(http, URL).send(batch, "cid")


@respx.mock
async def test_409_e_corpo_nao_json_contam_como_aceito() -> None:
    [batch] = group_bills([bill("c1")])
    respx.post(URL).mock(side_effect=[httpx.Response(409, text="já recebido"), httpx.Response(200, json=["ok"])])
    async with httpx.AsyncClient() as http:
        client = RentalClient(http, URL)
        assert await client.send(batch, "cid") == {}
        assert await client.send(batch, "cid") == {"response": ["ok"]}


@respx.mock
async def test_dispatcher_marca_lote_e_emite_evento(repo: Repository) -> None:
    batch_id = saved_batch(repo)
    respx.post(URL).mock(return_value=httpx.Response(202, json={"protocol": "L-1"}))
    env = Envelope(type="batch.ready", producer="api", correlation_id="cid-9", payload={"batch_id": batch_id})
    async with httpx.AsyncClient() as http:
        client = RentalClient(http, URL)
        out = await dispatch(env, repo, client)
        assert out is not None and out.type == "batch.sent" and out.payload["ack"] == {"protocol": "L-1"}
        assert out.correlation_id == "cid-9"
        assert repo.get_batch(batch_id).status is BatchStatus.ACKED  # type: ignore[union-attr]
        assert await dispatch(env, repo, client) is None  # já enviado
        assert await dispatch(env.model_copy(update={"payload": {"batch_id": "x"}}), repo, client) is None


@respx.mock
async def test_dispatcher_recusa_definitiva_vira_failed(repo: Repository) -> None:
    batch_id = saved_batch(repo)
    respx.post(URL).mock(return_value=httpx.Response(400, text="imóvel desconhecido"))
    env = Envelope(type="batch.ready", producer="api", payload={"batch_id": batch_id})
    async with httpx.AsyncClient() as http:
        out = await dispatch(env, repo, RentalClient(http, URL))
    assert out is not None and out.type == "batch.failed" and "imóvel desconhecido" in out.payload["error"]
    assert {b.status for b in repo.list_bills()} == {BillStatus.FAILED}


def test_store_bill_avisa_revisao_manual(repo: Repository) -> None:
    linked = Envelope(type="bill.normalized", producer="n", payload={"bill": bill("c1").model_dump(mode="json")})
    unlinked = Envelope(type="bill.normalized", producer="n", payload={"bill": bill("c2", None).model_dump(mode="json")})
    assert store_bill(linked, repo) is None
    out = store_bill(unlinked, repo)
    assert out is not None and out.type == "bill.manual_review" and out.payload["account_number"] == "c2"
    assert store_bill(unlinked, repo) is None  # duplicada
