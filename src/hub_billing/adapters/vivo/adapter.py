from hub_billing.adapters.base import (
    ProviderAccount,
    ProviderAdapter,
    ProviderCredentials,
    ProviderSession,
    RawBill,
)
from hub_billing.adapters.registry import register
from hub_billing.domain.models import UtilityType


@register("vivo")
class VivoAdapter(ProviderAdapter):
    """Telecom: contas Vivo (fixo, internet e móvel), identificadas pelo código do cliente.

    Coleta disponível: importação da fatura em PDF (parse_invoice / raw_from_invoice).
    A coleta automática (portal, API ou EDI) depende do épico E1; até lá os métodos abaixo não estão implementados.
    """

    utility_type = UtilityType.TELECOM
    account_labels = ("número da conta", "nº da conta", "código do cliente", "conta")

    async def authenticate(self, credentials: ProviderCredentials) -> ProviderSession:
        raise NotImplementedError("E1: definir meio de coleta da Vivo")

    async def list_accounts(self, session: ProviderSession) -> list[ProviderAccount]:
        raise NotImplementedError("E1: definir meio de coleta da Vivo")

    async def fetch_bills(
        self, session: ProviderSession, account: ProviderAccount, reference: str | None = None
    ) -> list[RawBill]:
        raise NotImplementedError("E1: definir meio de coleta da Vivo")

    async def download_document(self, session: ProviderSession, bill: RawBill) -> bytes:
        raise NotImplementedError("E1: definir meio de coleta da Vivo")
