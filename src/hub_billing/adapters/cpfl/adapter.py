import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import jwt

from hub_billing.adapters.base import (
    ProviderAccount,
    ProviderAdapter,
    ProviderCredentials,
    ProviderSession,
    RawBill,
    SessionExpired,
)
from hub_billing.adapters.cpfl import agencia_virtual as av
from hub_billing.adapters.registry import register
from hub_billing.collection.values import parse_date, parse_money, parse_reference
from hub_billing.domain.models import UtilityType

log = logging.getLogger(__name__)
DISTRIBUTORS = {"paulista", "piratininga", "santa-cruz", "rge"}
API = "https://servicosonline.cpfl.com.br/agencia-webapi/api"
WEBAPP = "https://servicosonline.cpfl.com.br/agencia-webapp/"
CLIENT_ID = "agencia-virtual-cpfl-web"
_INSTALLATION_FIELDS = ("Instalacao", "ParceiroNegocio", "ContaContrato", "CodigoClasse", "CodEmpresaSAP", "CodigoFase",
                        "IndGrupoA", "Situacao")


def _as_obj(value: Any) -> Any:
    """A resposta do /api/token traz Instalacao e ListaInstalacoes como strings JSON."""
    return json.loads(value) if isinstance(value, str) and value[:1] in "[{" else value


def _token_expiry(token: str) -> datetime | None:
    try:
        exp = jwt.decode(token, options={"verify_signature": False}).get("exp")
    except jwt.PyJWTError:
        return None
    return datetime.fromtimestamp(exp, UTC) if exp else None


@register("cpfl")
class CpflAdapter(ProviderAdapter):
    """Energia: distribuidoras do grupo CPFL, incluindo RGE (Rio Grande do Sul), todas na mesma Agência Virtual.

    Coleta automática pela API da Agência Virtual. O login (Azure B2C com MFA) é interativo, então a sessão vem do
    login assistido (tools/login_portal.py cpfl), que guarda a resposta do /api/token; depois o hub renova sozinho
    com o refresh token. Também aceita a importação da fatura em PDF (parse_invoice / raw_from_invoice).
    """

    utility_type = UtilityType.ENERGY
    account_labels = ("seu código", "código da instalação", "nº da instalação", "instalação", "unidade consumidora", "uc")

    def __init__(self, distributor: str | None = None) -> None:
        if distributor is not None and distributor not in DISTRIBUTORS:
            raise ValueError(f"distribuidora CPFL desconhecida: {distributor}")
        super().__init__(distributor)

    def _headers(self, session: ProviderSession, auth: bool = True) -> dict[str, str]:
        headers = {"Accept": "application/json", "Origin": session.data.get("origin") or "https://servicosonline.cpfl.com.br",
                   "Referer": session.data.get("referer") or WEBAPP}
        if session.data.get("user_agent"):
            headers["User-Agent"] = session.data["user_agent"]
        if session.data.get("cookies"):  # cookies do Cloudflare capturados no navegador do login assistido
            headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in session.data["cookies"].items())
        if auth:
            headers["Authorization"] = f"Bearer {session.data['access_token']}"
        return headers

    @staticmethod
    def _api(session: ProviderSession) -> str:
        """API vista no login assistido (o site novo, cpfl.com.br/agencia, pode chamar outro endereço)."""
        return str(session.data.get("api_base") or API)

    async def authenticate(self, credentials: ProviderCredentials) -> ProviderSession:
        raise NotImplementedError("CPFL/RGE usa login B2C com MFA: rode tools/login_portal.py cpfl")

    async def resume(self, data: dict[str, Any]) -> ProviderSession:
        session = ProviderSession(provider=self.code, data=dict(data))
        if data.get("site") == "agencia-virtual" and not data.get("access_token"):  # site novo: sessão em cookies
            return session
        expiry = _token_expiry(data.get("access_token", ""))
        if expiry and expiry > datetime.now(UTC) + timedelta(minutes=2):
            return session
        if not data.get("refresh_token"):
            raise SessionExpired("sessão da CPFL expirada: refaça o login assistido")
        resp = await self._client().post(f"{self._api(session)}/token", headers=self._headers(session, auth=False), data={
            "client_id": data.get("clientId") or CLIENT_ID, "grant_type": "refresh_token",
            "refresh_token": data["refresh_token"]})
        if resp.status_code != 200:
            raise SessionExpired(f"renovação do token da CPFL recusada (HTTP {resp.status_code}): refaça o login assistido")
        session.data.update(resp.json())
        return session

    def _site(self, session: ProviderSession) -> bool:
        return session.data.get("site") == "agencia-virtual" and not session.data.get("access_token")

    async def _site_get(self, session: ProviderSession, url: str) -> httpx.Response:
        return await self._site_request(session, "GET", url)

    async def _site_request(self, session: ProviderSession, method: str, url: str, data: dict[str, str] | None = None,
                            ajax: bool = False) -> httpx.Response:
        """Pedido ao site novo com os cookies da sessão; guarda os cookies que o site renovar no caminho."""
        cookies = session.data["cookies"] = dict(session.data.get("cookies") or {})
        headers = {"Cookie": "; ".join(f"{k}={v}" for k, v in cookies.items())}
        if session.data.get("user_agent"):
            headers["User-Agent"] = session.data["user_agent"]
        if ajax:
            headers.update({"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"})
        resp = await self._client().request(method, url, data=data, headers=headers, follow_redirects=True)
        for step in (*resp.history, resp):
            cookies.update({k: v for k, v in step.cookies.items() if v})
        if resp.status_code in (401, 403):
            raise SessionExpired(f"a CPFL recusou a sessão (HTTP {resp.status_code}): refaça o login assistido")
        resp.raise_for_status()
        return resp

    async def _site_form(self, session: ProviderSession, pn: str | None = None) -> tuple[dict[str, Any], str] | None:
        """Abre a escolha de UC e carrega, por AJAX, a lista de UCs do titular. None se a página mudou."""
        resp = await self._site_get(session, av.SELECAO)
        form = av.parse_selecao(resp.text)
        if form is None:
            return None
        pn = pn or form["parceiro_negocio"]
        ajax = await self._site_request(session, "POST", av.SELECAO_AJAX, ajax=True, data={
            "parceiro_negocio": pn, "form_build_id": form["form_build_id"], "form_id": form["form_id"],
            "_triggering_element_name": "parceiro_negocio", "_drupal_ajax": "1", **form["ajax_page_state"]})
        try:
            fragment, build_id = av.parse_ajax(ajax.json())
        except ValueError:
            fragment, build_id = "", None
        form.update(parceiro_negocio=pn, form_build_id=build_id or form["form_build_id"], fragment=fragment)
        return form, fragment

    async def _site_choose(self, session: ProviderSession, escolha: dict[str, str]) -> None:
        """Troca a UC da sessão no site (botão Acessar da página de escolha)."""
        loaded = await self._site_form(session, escolha.get("pn"))
        if loaded is None:
            raise ValueError("a página de escolha de UC da CPFL mudou")
        form, _ = loaded
        await self._site_request(session, "POST", av.SELECAO, data={
            "parceiro_negocio": form["parceiro_negocio"], escolha["campo"]: escolha["valor"],
            "form_build_id": form["form_build_id"], "form_id": form["form_id"], "op": "Acessar"})

    async def _site_choices(self, session: ProviderSession) -> list[dict[str, str]]:
        """Todas as UCs do login, de todos os titulares. Vazio quando não deu para ler a lista."""
        loaded = await self._site_form(session)
        if loaded is None:
            return []
        form, fragment = loaded
        pns = [form["parceiro_negocio"]]
        if form.get("pns_url"):
            try:
                resp = await self._site_request(session, "GET", f"{av.SITE}{form['pns_url']}", ajax=True)
                pns = list(dict.fromkeys([*pns, *av.parse_pns(resp.json())]))
            except (httpx.HTTPError, ValueError):
                pass
        choices = [{**c, "pn": pns[0]} for c in av.parse_ucs(fragment)]
        for pn in pns[1:]:
            other = await self._site_form(session, pn)
            choices += [{**c, "pn": pn} for c in av.parse_ucs(other[1])] if other else []
        if not choices:
            av.save_diagnostic("selecao-ucs.html", fragment or "(lista de UCs vazia)")
        return choices

    async def list_accounts(self, session: ProviderSession) -> list[ProviderAccount]:
        if self._site(session):
            try:
                choices = await self._site_choices(session)
            except httpx.HTTPError as exc:  # sem a lista, coleta ao menos a UC aberta no login
                log.warning("não deu para listar as UCs da CPFL: %s", exc)
                choices = []
            accounts: dict[str, ProviderAccount] = {}
            options: list[dict[str, str] | None] = list(choices) or [None]
            for escolha in options:
                if escolha is not None:
                    await self._site_choose(session, escolha)
                conta = await self._site_conta_atual(session)
                if conta:
                    extra = {"conta_atual": conta, **({"escolha": escolha} if escolha else {})}
                    accounts.setdefault(conta["instalacao"], ProviderAccount(account_number=conta["instalacao"],
                                                                             extra=extra))
            return list(accounts.values())
        found: dict[str, ProviderAccount] = {}
        candidates = [_as_obj(session.data.get("Instalacao")), *(_as_obj(session.data.get("ListaInstalacoes")) or [])]
        for inst in candidates:
            if isinstance(inst, dict) and inst.get("Instalacao") and inst.get("ParceiroNegocio"):
                number = str(inst["Instalacao"])
                found.setdefault(number, ProviderAccount(account_number=number, extra=dict(inst)))
        return list(found.values())

    async def _site_conta_atual(self, session: ProviderSession) -> dict[str, Any] | None:
        resp = await self._site_get(session, av.PAGINA_INICIAL)
        if av.is_login_page(str(resp.url), resp.text):
            raise SessionExpired("sessão da CPFL expirada: refaça o login assistido")
        return av.parse_conta_atual(resp.text)

    async def fetch_bills(
        self, session: ProviderSession, account: ProviderAccount, reference: str | None = None
    ) -> list[RawBill]:
        """Contas em aberto da instalação (historico-contas/validar-situacao); no site novo, a conta atual da UC."""
        if "conta_atual" in account.extra:
            c = account.extra["conta_atual"]
            if reference and c["referencia"] != reference:
                return []
            return [RawBill(provider=self.code, account_number=account.account_number, reference_month=c["referencia"],
                            due_date=parse_date(c["vencimento"]), amount=parse_money(c["valor"]),
                            payload={"source": "agencia-virtual", "digitable_line": c["codigo_barras"],
                                     "status": c["situacao"], "numeroContaEnergia": c["numero_conta"],
                                     **({"escolha": account.extra["escolha"]} if account.extra.get("escolha") else {})})]
        body = {"RetornarDetalhes": True, "GerarProtocolo": False,
                **{k: account.extra.get(k) for k in _INSTALLATION_FIELDS}}
        resp = await self._client().post(f"{self._api(session)}/historico-contas/validar-situacao", json=body,
                                         headers=self._headers(session))
        if resp.status_code == 401:
            raise SessionExpired("token da CPFL recusado: refaça o login assistido")
        resp.raise_for_status()
        bills = []
        for row in resp.json().get("ContasAberto") or []:
            ref = parse_reference(row.get("MesReferencia") or row.get("MesRef"))
            if reference and ref != reference:
                continue
            bills.append(RawBill(
                provider=self.code, account_number=account.account_number, reference_month=ref,
                due_date=parse_date(row["Vencimento"]), amount=parse_money(row.get("Valor") or row.get("Total")),
                payload={"source": "api", "digitable_line": row.get("CodigoBarras") or row.get("CodBarras"),
                         "pix_code": row.get("strEstaticoPIX"), "status": row.get("Status"),
                         "numeroContaEnergia": row.get("NumeroContaEnergia") or row.get("NumContaEnergia"),
                         "contaAcumulada": bool(row.get("ContaAcumulada")),
                         "installation": {k: account.extra.get(k) for k in _INSTALLATION_FIELDS}}))
        return bills

    async def download_document(self, session: ProviderSession, bill: RawBill) -> bytes:
        p = bill.payload
        if p.get("source") == "agencia-virtual":
            if p.get("escolha"):  # a conta completa é sempre da UC aberta na sessão
                await self._site_choose(session, p["escolha"])
            resp = await self._site_get(session, av.CONTA_COMPLETA)
            if "pdf" not in resp.headers.get("content-type", ""):
                raise ValueError("a CPFL não devolveu o PDF da conta completa")
            return resp.content
        inst = p.get("installation", {})
        resp = await self._client().get(f"{self._api(session)}/historico-contas/conta-completa-pdf",
                                        headers=self._headers(session, auth=False),
                                        params={"numeroContaEnergia": p.get("numeroContaEnergia"),
                                                "codigoClasse": inst.get("CodigoClasse"),
                                                "codEmpresaSAP": inst.get("CodEmpresaSAP"),
                                                "instalacao": bill.account_number,
                                                "parceiroNegocio": inst.get("ParceiroNegocio"),
                                                "token": session.data["access_token"],
                                                "contaAcumulada": str(p.get("contaAcumulada", False)).lower()})
        if resp.status_code == 401:
            raise SessionExpired("token da CPFL recusado: refaça o login assistido")
        resp.raise_for_status()
        return resp.content
