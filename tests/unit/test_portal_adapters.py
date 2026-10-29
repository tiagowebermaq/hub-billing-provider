"""Adapters CPFL/RGE e Aegea/Corsan contra respostas simuladas no formato lido dos bundles dos portais."""
import base64
import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import httpx
import jwt
import pytest
import respx

from hub_billing.adapters.aegea.adapter import API as AEGEA_API
from hub_billing.adapters.aegea.adapter import B2C_TOKEN
from hub_billing.adapters.base import ProviderAccount, RawBill, SessionExpired
from hub_billing.adapters.cpfl.adapter import API as CPFL_API
from hub_billing.adapters.registry import get_adapter, load_builtin
from hub_billing.collection.values import parse_date, parse_money, parse_reference
from hub_billing.infra.repository import Repository
from hub_billing.messaging.envelope import Envelope
from hub_billing.workers.handlers import collect

load_builtin()

INSTALACAO = {"Instalacao": "40012345", "ParceiroNegocio": "PN1", "ContaContrato": "CC1", "CodigoClasse": "B1",
              "CodEmpresaSAP": "RGE", "CodigoFase": "M", "IndGrupoA": "N", "Situacao": "LIGADA"}


def jwt_exp(minutes: int) -> str:
    return jwt.encode({"exp": datetime.now(UTC) + timedelta(minutes=minutes)}, "x" * 32)


def cpfl_session(minutes: int = 30) -> dict[str, object]:
    return {"access_token": jwt_exp(minutes), "refresh_token": "rt-1", "Instalacao": json.dumps(INSTALACAO),
            "ListaInstalacoes": json.dumps([INSTALACAO, {**INSTALACAO, "Instalacao": "40099999"}, {"Instalacao": "x"}]),
            "user_agent": "Mozilla/5.0", "cookies": {"cf_clearance": "abc"}}


def test_conversoes() -> None:
    assert parse_date("20/11/2026") == parse_date("2026-11-20T00:00:00") == parse_date("2026-11-20") == date(2026, 11, 20)
    assert parse_money("1.234,56") == parse_money(1234.56) == parse_money("1234.56") == Decimal("1234.56")
    assert parse_reference("10/2026") == parse_reference("2026-10") == "2026-10"
    assert parse_reference("9/2026") == "2026-09"
    for fn, bad in ((parse_date, "ontem"), (parse_money, "abc"), (parse_reference, "outubro")):
        with pytest.raises(ValueError):
            fn(bad)


@respx.mock
async def test_cpfl_coleta_e_pdf() -> None:
    validar = respx.post(f"{CPFL_API}/historico-contas/validar-situacao").mock(return_value=httpx.Response(200, json={
        "ContasAberto": [
            {"MesReferencia": "10/2026", "Vencimento": "20/11/2026", "Valor": "150,00", "CodigoBarras": "8366...",
             "NumeroContaEnergia": "999", "strEstaticoPIX": "000201", "Status": "A VENCER"},
            {"MesRef": "09/2026", "Vencimento": "2026-10-20T00:00:00", "Total": "98,10", "CodBarras": "8366..."}]}))
    pdf = respx.get(f"{CPFL_API}/historico-contas/conta-completa-pdf").mock(return_value=httpx.Response(200, content=b"%PDF"))
    async with httpx.AsyncClient() as http:
        adapter = get_adapter("cpfl", "rge").use(http)
        session = await adapter.resume(cpfl_session())
        accounts = await adapter.list_accounts(session)
        assert [a.account_number for a in accounts] == ["40012345", "40099999"]
        bills = await adapter.fetch_bills(session, accounts[0])
        assert [(b.reference_month, b.amount) for b in bills] == [("2026-10", Decimal("150.00")), ("2026-09", Decimal("98.10"))]
        assert len(await adapter.fetch_bills(session, accounts[0], "2026-10")) == 1
        sent = json.loads(validar.calls.last.request.content)
        assert sent["Instalacao"] == "40012345" and sent["RetornarDetalhes"] is True
        headers = validar.calls.last.request.headers
        assert headers["Cookie"] == "cf_clearance=abc" and headers["User-Agent"] == "Mozilla/5.0"
        assert await adapter.download_document(session, bills[0]) == b"%PDF"
        params = pdf.calls.last.request.url.params
        assert params["numeroContaEnergia"] == "999" and params["token"] == session.data["access_token"]
        bill = adapter.to_canonical(bills[0])
        assert bill.distributor == "rge" and bill.pix_code == "000201" and bill.digitable_line == "8366..."


@respx.mock
async def test_cpfl_renova_token_e_expira() -> None:
    novo = jwt_exp(30)
    route = respx.post(f"{CPFL_API}/token").mock(side_effect=[httpx.Response(200, json={"access_token": novo,
                                                                                        "refresh_token": "rt-2"}),
                                                             httpx.Response(400)])
    async with httpx.AsyncClient() as http:
        adapter = get_adapter("cpfl").use(http)
        session = await adapter.resume(cpfl_session(minutes=-5))
        assert session.data["access_token"] == novo and session.data["refresh_token"] == "rt-2"
        assert b"grant_type=refresh_token" in route.calls.last.request.content
        with pytest.raises(SessionExpired):
            await adapter.resume(cpfl_session(minutes=-5))
        with pytest.raises(SessionExpired):
            await adapter.resume({"access_token": "nao-e-jwt"})
        respx.post(f"{CPFL_API}/historico-contas/validar-situacao").mock(return_value=httpx.Response(401))
        respx.get(f"{CPFL_API}/historico-contas/conta-completa-pdf").mock(return_value=httpx.Response(401))
        with pytest.raises(SessionExpired):
            await adapter.fetch_bills(session, ProviderAccount(account_number="1", extra=INSTALACAO))
        raw = RawBill(provider="cpfl", account_number="1", reference_month="2026-10", due_date=date(2026, 11, 1),
                      amount=Decimal("1"))
        with pytest.raises(SessionExpired):
            await adapter.download_document(session, raw)


@respx.mock
async def test_cpfl_usa_api_vista_no_login() -> None:
    nova = "https://api.cpfl.com.br/agencia-webapi/api"
    validar = respx.post(f"{nova}/historico-contas/validar-situacao").mock(
        return_value=httpx.Response(200, json={"ContasAberto": []}))
    data = {**cpfl_session(), "api_base": nova, "origin": "https://www.cpfl.com.br",
            "referer": "https://www.cpfl.com.br/agencia/"}
    async with httpx.AsyncClient() as http:
        adapter = get_adapter("cpfl", "rge").use(http)
        session = await adapter.resume(data)
        assert await adapter.fetch_bills(session, ProviderAccount(account_number="1", extra=INSTALACAO)) == []
    assert validar.calls.last.request.headers["Origin"] == "https://www.cpfl.com.br"


def test_cpfl_distribuidora_invalida() -> None:
    with pytest.raises(ValueError):
        get_adapter("cpfl", "xpto")


MATRICULAS = {"content": {"nomeCliente": "WEBERMAQ", "ligacoes": [
    {"numeroLigacao": 778899, "numeroSequencialResponsavel": 1, "zonaLigacao": 3,
     "unidade": {"nome": "Corsan", "tenantIDCorporativo": "tenant-uuid"}}]}}
DEBITOS = {"content": {"debitos": [
    {"referencia": "10/2026", "dataVencimento": "2026-11-10T00:00:00", "valorFatura": 80.1, "statusFatura": "Em Aberto",
     "codigoBarrasDigitavel": "8266...", "codigoPIX": "pix", "consumo": 12, "anoLancamento": 2026, "numeroAviso": 55,
     "zonaLigacao": 3},
    {"referencia": "09/2026", "dataVencimento": "10/10/2026", "valorFatura": "75,00"}]}}


def aegea_session(minutes: int = 30) -> dict[str, object]:
    return {"access_token": "at-1", "refresh_token": "rt-1",
            "expires_at": (datetime.now(UTC) + timedelta(minutes=minutes)).isoformat()}


@respx.mock
async def test_aegea_coleta_e_pdf() -> None:
    respx.get(f"{AEGEA_API}/cliente/matriculas").mock(return_value=httpx.Response(200, json=MATRICULAS))
    deb = respx.get(f"{AEGEA_API}/fatura/debito-totais/matricula").mock(return_value=httpx.Response(200, json=DEBITOS))
    pdf = respx.get(f"{AEGEA_API}/fatura/segunda-via/download").mock(
        return_value=httpx.Response(200, json={"content": {"bytes": base64.b64encode(b"%PDF-aegea").decode()}}))
    async with httpx.AsyncClient() as http:
        adapter = get_adapter("aegea", "corsan").use(http, apim_key="k")
        session = await adapter.resume(aegea_session())
        [account] = await adapter.list_accounts(session)
        assert account.account_number == "778899" and account.extra["tenant"] == "tenant-uuid"
        bills = await adapter.fetch_bills(session, account)
        assert [(b.reference_month, b.amount, b.due_date) for b in bills] == [
            ("2026-10", Decimal("80.10"), date(2026, 11, 10)), ("2026-09", Decimal("75.00"), date(2026, 10, 10))]
        assert await adapter.fetch_bills(session, account, "2026-10") == bills[:1]
        req = deb.calls.last.request
        assert req.headers["X-TenantID"] == "tenant-uuid" and req.headers["Ocp-Apim-Subscription-Key"] == "k"
        assert req.url.params["matricula"] == "778899" and req.url.params["zonaLigacao"] == "3"
        assert await adapter.download_document(session, bills[0]) == b"%PDF-aegea"
        assert pdf.calls.last.request.url.params["mesLancamento"] == "10"
        canonical = adapter.to_canonical(bills[0])
        assert canonical.consumption == 12 and canonical.consumption_unit == "m3" and canonical.pix_code == "pix"


@respx.mock
async def test_aegea_renova_e_expira() -> None:
    route = respx.post(B2C_TOKEN).mock(side_effect=[httpx.Response(200, json={"access_token": "at-2", "expires_in": 3600}),
                                                    httpx.Response(400)])
    async with httpx.AsyncClient() as http:
        adapter = get_adapter("aegea").use(http)
        session = await adapter.resume(aegea_session(minutes=-1))
        assert session.data["access_token"] == "at-2" and session.data["refresh_token"] == "rt-1"
        assert route.calls.last.request.headers["Origin"] == "https://cliente.corsan.com.br"
        with pytest.raises(SessionExpired):
            await adapter.resume(aegea_session(minutes=-1))
        with pytest.raises(SessionExpired):
            await adapter.resume({"access_token": "x"})
        respx.get(f"{AEGEA_API}/cliente/matriculas").mock(return_value=httpx.Response(401))
        with pytest.raises(SessionExpired):
            await adapter.list_accounts(session)


@pytest.fixture
def repo() -> Repository:
    r = Repository.from_url("sqlite+pysqlite:///:memory:", "")
    r.create_schema()
    return r


@respx.mock
async def test_coletor_publica_resultados_e_pede_login(repo: Repository) -> None:
    respx.get(f"{AEGEA_API}/cliente/matriculas").mock(return_value=httpx.Response(200, json=MATRICULAS))
    respx.get(f"{AEGEA_API}/fatura/debito-totais/matricula").mock(return_value=httpx.Response(200, json=DEBITOS))
    env = Envelope(type="collect.request", producer="api", correlation_id="cid-c",
                   payload={"provider": "aegea", "distributor": "corsan", "reference_month": "2026-10"})
    async with httpx.AsyncClient() as http:
        [login] = await collect(env, repo, http)
        assert login.type == "collect.login_required" and "login assistido" in login.payload["reason"]
        repo.save_session("aegea", "corsan", aegea_session(), None)
        [out] = await collect(env, repo, http, {"apim_key": "k"})
        assert out.type == "collect.result" and out.correlation_id == "cid-c"
        assert out.payload["raw"]["account_number"] == "778899" and out.payload["distributor"] == "corsan"
        other = env.model_copy(update={"payload": {**env.payload, "account_number": "1"}})
        assert await collect(other, repo, http) == []
        repo.save_session("aegea", "corsan", {"access_token": "x"}, None)
        [expired] = await collect(env, repo, http)
        assert expired.type == "collect.login_required"


def test_sessao_cifrada_e_chave_errada(repo: Repository) -> None:
    from cryptography.fernet import Fernet

    key = Fernet.generate_key().decode()
    r1 = Repository(repo.engine, __import__("hub_billing.core.security", fromlist=["SecretBox"]).SecretBox(key))
    r1.save_session("cpfl", "rge", {"refresh_token": "segredo"}, datetime(2026, 11, 1, tzinfo=UTC))
    with repo.engine.connect() as conn:
        stored = conn.exec_driver_sql("select data_enc from provider_sessions").scalar_one()
    assert "segredo" not in stored
    data, expires = r1.load_session("cpfl", "rge")  # type: ignore[misc]
    assert data == {"refresh_token": "segredo"} and expires == datetime(2026, 11, 1, tzinfo=UTC)
    assert repo.load_session("cpfl", "rge") is None  # outra chave
    assert repo.load_session("aegea", None) is None
    r1.save_session("cpfl", "rge", {"refresh_token": "novo"}, None)
    assert [s["provider"] for s in r1.list_sessions()] == ["cpfl"]
