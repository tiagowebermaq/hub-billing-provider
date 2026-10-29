from decimal import Decimal

import httpx
import pytest
import respx

from hub_billing.adapters.base import SessionExpired
from hub_billing.adapters.cpfl import agencia_virtual as av
from hub_billing.adapters.registry import get_adapter

PAGINA = """<html><head><link href="/css/conta-atual.css"></head><body>
<div class="coh-column conta-atual"><div class="card"><div class="card-body atrasada">
<p class="sub-title">Débito atual - Fatura </p><div class="valortotal-wrapper"> <p class="title">R$ 1.234,56</p> </div>
<p class="sub-title">Referente à Setembro de 2026 </p>
<div class="vencimento "> Vencimento: 20/10/2026 <span>atrasada</span> </div>
<a class="entenda-conta" href="/agencia-virtual/conta-completa"> Ver conta completa </a></div></div>
<div id="codigo-de-barra" class="modal" data-instalacao="4000123456" data-numeroconta="900000000001">
<div class="codigo"> 836900000012 345600863550 933682441011 100018827525 </div></div></div>
<div class="modal-footer"></div></body></html>"""
SESSAO = {"site": "agencia-virtual", "cookies": {"SSESSx": "abc"}, "user_agent": "Mozilla/5.0"}


def test_le_conta_atual_da_pagina_inicial() -> None:
    assert av.parse_conta_atual(PAGINA) == {
        "instalacao": "4000123456", "numero_conta": "900000000001", "valor": "1.234,56", "referencia": "2026-09",
        "vencimento": "20/10/2026", "situacao": "atrasada",
        "codigo_barras": "836900000012345600863550933682441011100018827525"}
    sem_situacao = PAGINA.replace(" <span>atrasada</span>", "")
    assert av.parse_conta_atual(sem_situacao)["situacao"] is None  # type: ignore[index]
    assert av.parse_conta_atual("<html>login</html>") is None
    assert av.parse_conta_atual(PAGINA.replace("Setembro", "Setembrino")) is None
    assert av.is_login_page("https://cpflb2cprd.b2clogin.com/x", "") and not av.is_login_page(av.PAGINA_INICIAL, PAGINA)


@respx.mock
async def test_coleta_pelo_site_novo() -> None:
    pagina = respx.get(av.PAGINA_INICIAL).mock(return_value=httpx.Response(200, text=PAGINA))
    respx.get(av.CONTA_COMPLETA).mock(return_value=httpx.Response(200, content=b"%PDF",
                                                                  headers={"content-type": "application/pdf"}))
    async with httpx.AsyncClient() as http:
        adapter = get_adapter("cpfl", "rge").use(http)
        session = await adapter.resume(SESSAO)
        [conta] = await adapter.list_accounts(session)
        assert conta.account_number == "4000123456"
        assert pagina.calls.last.request.headers["Cookie"] == "SSESSx=abc"
        [bill] = await adapter.fetch_bills(session, conta)
        assert (bill.reference_month, bill.amount, bill.due_date.isoformat()) == ("2026-09", Decimal("1234.56"),
                                                                                  "2026-10-20")
        assert bill.payload["digitable_line"].startswith("8369") and bill.payload["status"] == "atrasada"
        assert await adapter.fetch_bills(session, conta, "2026-10") == []
        assert await adapter.download_document(session, bill) == b"%PDF"
        assert adapter.to_canonical(bill).distributor == "rge"


@respx.mock
async def test_site_novo_sessao_expirada_ou_sem_pdf() -> None:
    async with httpx.AsyncClient() as http:
        adapter = get_adapter("cpfl", "rge").use(http)
        session = await adapter.resume(SESSAO)
        respx.get(av.PAGINA_INICIAL).mock(return_value=httpx.Response(200, text="<html>b2clogin</html>"))
        with pytest.raises(SessionExpired):
            await adapter.list_accounts(session)
        respx.get(av.PAGINA_INICIAL).mock(return_value=httpx.Response(403, text="Just a moment"))
        with pytest.raises(SessionExpired):
            await adapter.list_accounts(session)
        respx.get(av.PAGINA_INICIAL).mock(return_value=httpx.Response(200, text="<html>sem conta</html>"))
        assert await adapter.list_accounts(session) == []
        respx.get(av.CONTA_COMPLETA).mock(return_value=httpx.Response(200, text="<html></html>"))
        respx.get(av.PAGINA_INICIAL).mock(return_value=httpx.Response(200, text=PAGINA))
        [conta] = await adapter.list_accounts(session)
        [bill] = await adapter.fetch_bills(session, conta)
        with pytest.raises(ValueError):
            await adapter.download_document(session, bill)
