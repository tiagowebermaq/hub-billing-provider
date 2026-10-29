from hub_billing.adapters.base import (
    ProviderAccount,
    ProviderAdapter,
    ProviderCredentials,
    ProviderSession,
    RawBill,
)
from hub_billing.adapters.registry import register
from hub_billing.domain.models import UtilityType

DISTRIBUTORS = {"paulista", "piratininga", "santa-cruz", "rge"}


@register("cpfl")
class CpflAdapter(ProviderAdapter):
    """Energia: distribuidoras do grupo CPFL, incluindo RGE (Rio Grande do Sul).

    Coleta disponível: importação da fatura em PDF (parse_invoice / raw_from_invoice).
    A coleta automática (portal, API ou EDI) depende do épico E1; até lá os métodos abaixo não estão implementados.
    """

    utility_type = UtilityType.ENERGY
    account_labels = ("seu código", "código da instalação", "nº da instalação", "instalação", "unidade consumidora", "uc")

    def __init__(self, distributor: str | None = None) -> None:
        if distributor is not None and distributor not in DISTRIBUTORS:
            raise ValueError(f"distribuidora CPFL desconhecida: {distributor}")
        super().__init__(distributor)

    async def authenticate(self, credentials: ProviderCredentials) -> ProviderSession:
        raise NotImplementedError("E1: definir meio de coleta da CPFL/RGE")

    async def list_accounts(self, session: ProviderSession) -> list[ProviderAccount]:
        raise NotImplementedError("E1: definir meio de coleta da CPFL/RGE")

    async def fetch_bills(
        self, session: ProviderSession, account: ProviderAccount, reference: str | None = None
    ) -> list[RawBill]:
        raise NotImplementedError("E1: definir meio de coleta da CPFL/RGE")

    async def download_document(self, session: ProviderSession, bill: RawBill) -> bytes:
        raise NotImplementedError("E1: definir meio de coleta da CPFL/RGE")
