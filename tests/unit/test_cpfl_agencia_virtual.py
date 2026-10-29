from decimal import Decimal

import httpx
import pytest
import respx

from hub_billing.adapters.base import SessionExpired
from hub_billing.adapters.cpfl import agencia_virtual as av
from hub_billing.adapters.registry import get_adapter

PAGINA = """<html><head><link href="/css/conta-atual.css"></head><body>
<div class="user-logged-in"><div class="user-logged-in-name"><span title="fulano">fulano</span></div>
<div class="user-logged-in-role"><span title="Residencial / Baixa tensão">Residencial / Baixa tensão</span></div>
<div class="user-logged-in-info"><span title="R DAS FLORES, 10">R DAS FLORES, 10</span></div></div>
<div class="coh-column conta-atual"><div class="card"><div class="card-body atrasada">
<p class="sub-title">Débito atual - Fatura </p><div class="valortotal-wrapper"> <p class="title">R$ 1.234,56</p> </div>
<p class="sub-title">Referente à Setembro de 2026 </p>
<div class="vencimento "> Vencimento: 20/10/2026 <span>atrasada</span> </div>
<a class="entenda-conta" href="/agencia-virtual/conta-completa"> Ver conta completa </a></div></div>
<div id="codigo-de-barra" class="modal" data-instalacao="4000123456" data-numeroconta="900000000001">
<div class="codigo"> 836900000012 345600863550 933682441011 100018827525 </div></div></div>
<div id="codigo-pix" class="modal codigo-pix" data-instalacao="4000123456"><div class="btn fill copiar-codigo">
Copiar código PIX <div class="hidden codigo">00020101br.gov.bcb.pix0136abc5204000053039865802BR6304F87F</div></div></div>
<div class="modal-footer"></div></body></html>"""


@pytest.fixture(autouse=True)
def diagnostico_temporario(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Nunca grava em logs/ de verdade: no PC ali ficam as páginas reais usadas para ajustar a leitura."""
    monkeypatch.setattr(av, "DIAG_DIR", tmp_path)


SESSAO = {"site": "agencia-virtual", "cookies": {"SSESSx": "abc"}, "user_agent": "Mozilla/5.0"}


def test_le_conta_atual_da_pagina_inicial() -> None:
    assert av.parse_conta_atual(PAGINA) == {
        "instalacao": "4000123456", "numero_conta": "900000000001", "valor": "1.234,56", "referencia": "2026-09",
        "vencimento": "20/10/2026", "situacao": "atrasada",
        "codigo_barras": "836900000012345600863550933682441011100018827525", "endereco": "R DAS FLORES, 10",
        "classe": "Residencial / Baixa tensão", "pix": "00020101br.gov.bcb.pix0136abc5204000053039865802BR6304F87F"}
    sem_situacao = PAGINA.replace(" <span>atrasada</span>", "")
    assert av.parse_conta_atual(sem_situacao)["situacao"] is None  # type: ignore[index]
    assert av.parse_conta_atual("<html>login</html>") is None
    assert av.parse_conta_atual(PAGINA.replace("Setembro", "Setembrino")) is None
    assert av.is_login_page("https://cpflb2cprd.b2clogin.com/x", "") and not av.is_login_page(av.PAGINA_INICIAL, PAGINA)


@respx.mock
async def test_coleta_pelo_site_novo() -> None:
    respx.get(av.SELECAO).mock(return_value=httpx.Response(200, text="<html>outra página</html>"))
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
    respx.get(av.SELECAO).mock(return_value=httpx.Response(500))
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


SELECAO = """<form action="/agencia/area-cliente/selecionar-perfil-instalacao" method="post">
<input class="custom-select select-instalacao" data-url="/api/gestao-acesso/pns?perfilId=p1"
 data-custom-select-default="111" data-custom-select-default-text="00000000000 - MARIA TESTE"
 type="text" name="parceiro_negocio" value="111">
<input class="padrao-rf003" type="text" name="filtro_num_instalacao" value="">
<div id="edit-items"></div>
<input type="submit" name="op" value="Acessar">
<input type="hidden" name="form_build_id" value="form-abc">
<input type="hidden" name="form_id" value="cpflenergia_service_selecionar_perfil_instalacao_form"></form>
<script type="application/json" data-drupal-selector="drupal-settings-json">
{"ajaxPageState": {"theme": "cpfl_theme", "libraries": "x,y", "other": 1}}</script>"""


def _lista(*ucs: str) -> list[dict[str, str]]:
    radios = "".join(f'<input type="radio" name="instalacao" value="{u}"><label>UC {u}</label>' for u in ucs)
    return [{"command": "settings"}, {"command": "update_build_id", "old": "form-abc", "new": "form-def"},
            {"command": "insert", "method": "replaceWith", "data": f'<div id="instalacoes-wrapper">{radios}'
             '<input type="radio" name="filtro_status" value="true"></div>'}]


def test_le_formulario_e_lista_de_ucs() -> None:
    form = av.parse_selecao(SELECAO)
    assert form == {"form_build_id": "form-abc", "form_id": "cpflenergia_service_selecionar_perfil_instalacao_form",
                    "parceiro_negocio": "111", "titular": "MARIA TESTE", "pns_url": "/api/gestao-acesso/pns?perfilId=p1",
                    "ajax_page_state": {"ajax_page_state[theme]": "cpfl_theme", "ajax_page_state[libraries]": "x,y"}}
    assert av.parse_selecao("<html></html>") is None
    fragment, build_id = av.parse_ajax(_lista("4000000001", "4000000002", "4000000001"))
    assert build_id == "form-def"
    assert av.parse_ucs(fragment) == [{"campo": "instalacao", "valor": "4000000001"},
                                      {"campo": "instalacao", "valor": "4000000002"}]
    assert av.parse_ajax({"erro": 1}) == ("", None)
    assert av.parse_pns({"results": [{"id": "111", "text": "1 - ANA"}, {"id": "2"}, {"text": "sem id"}]}) == {
        "111": "ANA", "2": None}
    embutido = '<a href="#">x</a><iframe src="/arquivos/conta.PDF?x=1&amp;y=2"></iframe>'
    assert av.pdf_link(embutido) == "/arquivos/conta.PDF?x=1&y=2"
    assert av.pdf_link('<a href="javascript:pdf()">x</a>') is None


@respx.mock
async def test_coleta_todas_as_ucs_do_login() -> None:
    aberta = {"uc": "4000000001"}
    respx.get(av.SELECAO).mock(return_value=httpx.Response(200, text=SELECAO))
    respx.get(f"{av.SITE}/api/gestao-acesso/pns").mock(return_value=httpx.Response(
        200, json={"results": [{"id": "111"}, {"id": "222", "text": "22200000000 - JOSE TESTE"}]}))

    def ajax(request: httpx.Request) -> httpx.Response:
        pn = dict(httpx.QueryParams(request.content.decode()))["parceiro_negocio"]
        return httpx.Response(200, json=_lista("4000000001", "4000000002") if pn == "111" else _lista("4000000003"))

    def acessar(request: httpx.Request) -> httpx.Response:
        form = dict(httpx.QueryParams(request.content.decode()))
        assert form["op"] == "Acessar" and form["form_build_id"] == "form-def"
        aberta["uc"] = form["instalacao"]
        return httpx.Response(302, headers=[("location", "/agencia-virtual/pagina-inicial"),
                                            ("set-cookie", "SSESSx=novo; path=/"),
                                            ("set-cookie", "Velho=; Max-Age=0; path=/")])

    respx.post(av.SELECAO_AJAX).mock(side_effect=ajax)
    respx.post(av.SELECAO).mock(side_effect=acessar)
    def inicial(request: httpx.Request) -> httpx.Response:
        if aberta["uc"] != "4000000001":  # depois da troca, o site só aceita o cookie renovado
            assert request.headers["Cookie"] == "SSESSx=novo"
        return httpx.Response(200, text=PAGINA.replace("4000123456", aberta["uc"]))

    respx.get(av.PAGINA_INICIAL).mock(side_effect=inicial)
    respx.get(av.CONTA_COMPLETA).mock(side_effect=lambda r: httpx.Response(
        200, content=aberta["uc"].encode(), headers={"content-type": "application/pdf"}))
    async with httpx.AsyncClient() as http:
        adapter = get_adapter("cpfl", "rge").use(http)
        session = await adapter.resume(dict(SESSAO, cookies={"SSESSx": "abc", "Velho": "1"}))
        contas = await adapter.list_accounts(session)
        assert [c.account_number for c in contas] == ["4000000001", "4000000002", "4000000003"]
        assert contas[2].extra["escolha"] == {"campo": "instalacao", "valor": "4000000003", "pn": "222",
                                              "titular": "JOSE TESTE"}
        assert session.data["cookies"]["SSESSx"] == "novo"  # cookie renovado volta para a sessão salva
        [bill] = await adapter.fetch_bills(session, contas[0])
        assert bill.payload["consumer"] == {"name": "MARIA TESTE"} and bill.payload["pix_code"].startswith("0002")
        assert bill.payload["property"] == {"address": "R DAS FLORES, 10", "category": "Residencial / Baixa tensão"}
        assert await adapter.download_document(session, bill) == b"4000000001"  # baixado com a UC 1 aberta
        canonical = adapter.to_canonical(bill)
        assert canonical.consumer.name == "MARIA TESTE" and canonical.property_info.address  # type: ignore[union-attr]


@respx.mock
async def test_lista_de_ucs_ilegivel_coleta_a_uc_aberta(tmp_path) -> None:  # type: ignore[no-untyped-def]
    sem_titulares = SELECAO.replace(' data-url="/api/gestao-acesso/pns?perfilId=p1"', "")
    respx.get(av.SELECAO).mock(return_value=httpx.Response(200, text=sem_titulares))
    respx.post(av.SELECAO_AJAX).mock(return_value=httpx.Response(200, text="não é json"))
    respx.get(av.PAGINA_INICIAL).mock(return_value=httpx.Response(200, text=PAGINA))
    respx.get(av.CONTA_COMPLETA).mock(return_value=httpx.Response(200, text="<html>sem pdf</html>"))
    async with httpx.AsyncClient() as http:
        adapter = get_adapter("cpfl", "rge").use(http)
        [conta] = await adapter.list_accounts(await adapter.resume(SESSAO))
    assert conta.account_number == "4000123456" and "escolha" not in conta.extra
    assert (tmp_path / "selecao-ucs.html").exists() and (tmp_path / "conta-completa.html").exists()


@respx.mock
async def test_conta_completa_em_pagina_segue_o_pdf_embutido() -> None:
    respx.get(av.CONTA_COMPLETA).mock(return_value=httpx.Response(200, text='<embed src="/arquivos/x.pdf">'))
    respx.get(f"{av.SITE}/arquivos/x.pdf").mock(return_value=httpx.Response(200, content=b"%PDF-embutido"))
    async with httpx.AsyncClient() as http:
        adapter = get_adapter("cpfl", "rge").use(http)
        assert await adapter._site_pdf(await adapter.resume(SESSAO)) == b"%PDF-embutido"


@respx.mock
async def test_troca_de_uc_que_cai_no_login_pede_novo_login() -> None:
    respx.get(av.PAGINA_INICIAL).mock(return_value=httpx.Response(302, headers={"location": "/b2c-auth/login"}))
    async with httpx.AsyncClient() as http:
        adapter = get_adapter("cpfl", "rge").use(http)
        session = await adapter.resume(SESSAO)
        with pytest.raises(SessionExpired, match="login de novo"):
            await adapter._site_get(session, av.PAGINA_INICIAL)
        respx.get(av.SELECAO).mock(return_value=httpx.Response(302, headers={"location": av.SELECAO}))
        with pytest.raises(ValueError, match="redirecionou demais"):
            await adapter._site_get(session, av.SELECAO)


def test_cookies_apagados_e_vencidos_saem_da_sessao() -> None:
    from hub_billing.adapters.cpfl.adapter import _update_cookies

    cookies = {"a": "1", "b": "2", "c": "3", "d": "4"}
    resp = httpx.Response(200, request=httpx.Request("GET", av.SITE), headers=[
        ("set-cookie", "a=; path=/"), ("set-cookie", "b=x; Expires=Thu, 01 Jan 1970 00:00:00 GMT"),
        ("set-cookie", "c=novo; Expires=Fri, 01 Jan 2100 00:00:00 GMT; HttpOnly"), ("set-cookie", "d=deleted"),
        ("set-cookie", "e=5; Expires=data ruim"), ("set-cookie", "=semnome")])
    _update_cookies(cookies, resp)
    assert cookies == {"c": "novo", "e": "5"}
