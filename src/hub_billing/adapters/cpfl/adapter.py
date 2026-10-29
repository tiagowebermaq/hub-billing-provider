import json
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from hub_billing.adapters.base import (
    ProviderAccount,
    ProviderAdapter,
    ProviderCredentials,
    ProviderSession,
    RawBill,
    SessionExpired,
)
from hub_billing.adapters.registry import register
from hub_billing.collection.values import parse_date, parse_money, parse_reference
from hub_billing.domain.models import UtilityType

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

    async def list_accounts(self, session: ProviderSession) -> list[ProviderAccount]:
        found: dict[str, ProviderAccount] = {}
        candidates = [_as_obj(session.data.get("Instalacao")), *(_as_obj(session.data.get("ListaInstalacoes")) or [])]
        for inst in candidates:
            if isinstance(inst, dict) and inst.get("Instalacao") and inst.get("ParceiroNegocio"):
                number = str(inst["Instalacao"])
                found.setdefault(number, ProviderAccount(account_number=number, extra=dict(inst)))
        return list(found.values())

    async def fetch_bills(
        self, session: ProviderSession, account: ProviderAccount, reference: str | None = None
    ) -> list[RawBill]:
        """Contas em aberto da instalação (historico-contas/validar-situacao)."""
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
