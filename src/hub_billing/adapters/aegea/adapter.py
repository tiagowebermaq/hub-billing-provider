import base64
from datetime import UTC, datetime, timedelta
from typing import Any

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

API = "https://api.aegea.com.br/external/agencia-virtual/app/v1"
PORTAL = "https://cliente.corsan.com.br"
B2C_TOKEN = "https://aeclientes.b2clogin.com/aeclientes.onmicrosoft.com/B2C_1A_SIGNUP_SIGNIN/oauth2/v2.0/token"
CLIENT_ID = "b673d12b-6c65-45b4-9c3c-299cc574b4f8"
SCOPE = "https://aeclientes.onmicrosoft.com/769f3922-02f3-4a34-9145-d73e3ece30e4/AguasAPI.ReadWrite offline_access openid"


def _detail(resp: Any) -> str:
    """Motivo da recusa (mensagem do APIM ou da API), curto, para o histórico de coletas."""
    try:
        body = resp.json()
        text = str(body.get("message") or body.get("mensagem") or body.get("error") or "") if isinstance(body, dict) else ""
    except ValueError:
        text = resp.text
    return " ".join(text.split())[:160] or f"HTTP {resp.status_code}"


@register("aegea")
class AegeaAdapter(ProviderAdapter):
    """Água e esgoto: unidades do grupo Aegea, incluindo Corsan (distributor="corsan").

    Coleta automática pela API da Agência Virtual (Azure APIM). O login B2C é interativo, então o token vem do
    login assistido (tools/login_portal.py aegea); o refresh token de SPA vale ~24 h.
    Opção obrigatória em use(): apim_key (HUB_AEGEA_APIM_KEY, chave pública do portal).
    Também aceita a importação da fatura em PDF (parse_invoice / raw_from_invoice).
    """

    utility_type = UtilityType.WATER
    account_labels = ("matrícula", "matricula", "código do imóvel", "inscrição")

    def _headers(self, session: ProviderSession, tenant: str | None = None) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {session.data['access_token']}", "Origin": PORTAL, "Referer": PORTAL + "/",
                   "Ocp-Apim-Subscription-Key": self.options.get("apim_key", ""), "Accept": "application/json"}
        if tenant:
            headers["X-TenantID"] = tenant
        return headers

    async def authenticate(self, credentials: ProviderCredentials) -> ProviderSession:
        raise NotImplementedError("Aegea usa login B2C interativo: rode tools/login_portal.py aegea")

    async def resume(self, data: dict[str, Any]) -> ProviderSession:
        session = ProviderSession(provider=self.code, data=dict(data))
        expires = data.get("expires_at")
        if expires and datetime.fromisoformat(expires) > datetime.now(UTC) + timedelta(minutes=2):
            return session
        return await self._refresh(session)

    async def _refresh(self, session: ProviderSession) -> ProviderSession:
        data = session.data
        if not data.get("refresh_token"):
            raise SessionExpired("sessão da Aegea expirada: refaça o login assistido")
        resp = await self._client().post(B2C_TOKEN, headers={"Origin": PORTAL}, data={
            "grant_type": "refresh_token", "client_id": CLIENT_ID, "refresh_token": data["refresh_token"], "scope": SCOPE})
        if resp.status_code != 200:
            raise SessionExpired(f"renovação do token da Aegea recusada (HTTP {resp.status_code}): refaça o login assistido")
        tok = resp.json()
        session.data.update(access_token=tok["access_token"], refresh_token=tok.get("refresh_token", data["refresh_token"]),
                            expires_at=(datetime.now(UTC) + timedelta(seconds=int(tok.get("expires_in", 3600)))).isoformat())
        return session

    async def _get(self, session: ProviderSession, path: str, tenant: str | None = None,
                   params: dict[str, Any] | None = None, retry: bool = True) -> Any:
        resp = await self._client().get(f"{API}/{path}", headers=self._headers(session, tenant), params=params)
        if resp.status_code == 401:
            detail = _detail(resp)
            if "subscription key" in detail.lower():  # recusa do APIM, não do login
                raise RuntimeError(f"a Aegea recusou a chave HUB_AEGEA_APIM_KEY do .env ({detail})")
            if not retry or not session.data.get("refresh_token"):
                raise SessionExpired(f"token da Aegea recusado ({detail}): refaça o login assistido")
            try:  # token vencido antes da hora: renova uma vez e tenta de novo
                await self._refresh(session)
            except SessionExpired as exc:
                raise SessionExpired(f"token da Aegea recusado ({detail}) e {exc}") from exc
            return await self._get(session, path, tenant, params, retry=False)
        resp.raise_for_status()
        return resp.json().get("content")

    async def list_accounts(self, session: ProviderSession) -> list[ProviderAccount]:
        content = await self._get(session, "cliente/matriculas") or {}
        return [ProviderAccount(
            account_number=str(lig["numeroLigacao"]),
            holder=content.get("nomeCliente"),
            extra={"seq": lig.get("numeroSequencialResponsavel"), "zona": lig.get("zonaLigacao"),
                   "tenant": (lig.get("unidade") or {}).get("tenantIDCorporativo"),
                   "unidade": (lig.get("unidade") or {}).get("nome")},
        ) for lig in content.get("ligacoes", [])]

    async def fetch_bills(
        self, session: ProviderSession, account: ProviderAccount, reference: str | None = None
    ) -> list[RawBill]:
        """Faturas em aberto da matrícula (debito-totais); com reference, só a competência pedida."""
        x = account.extra
        content = await self._get(session, "fatura/debito-totais/matricula", x.get("tenant"), {
            "matricula": account.account_number, "sequencialResponsavel": x.get("seq"), "zonaLigacao": x.get("zona")}) or {}
        bills = []
        for d in content.get("debitos", []):
            ref = parse_reference(d["referencia"])
            if reference and ref != reference:
                continue
            bills.append(RawBill(
                provider=self.code, account_number=account.account_number, reference_month=ref,
                due_date=parse_date(d["dataVencimento"]), amount=parse_money(d["valorFatura"]),
                payload={"source": "api", "digitable_line": d.get("codigoBarrasDigitavel"), "pix_code": d.get("codigoPIX"),
                         "consumption": d.get("consumo"), "status": d.get("statusFatura"), "tenant": x.get("tenant"),
                         "zona": d.get("zonaLigacao", x.get("zona")), "numeroAviso": d.get("numeroAviso"),
                         "anoLancamento": d.get("anoLancamento")}))
        return bills

    async def download_document(self, session: ProviderSession, bill: RawBill) -> bytes:
        p = bill.payload
        content = await self._get(session, "fatura/segunda-via/download", p.get("tenant"), {
            "matricula": bill.account_number, "zonaLigacao": p.get("zona"), "numeroAviso": p.get("numeroAviso"),
            "anoLancamento": p.get("anoLancamento"), "mesLancamento": int(bill.reference_month[5:])}) or {}
        return base64.b64decode(content["bytes"])
