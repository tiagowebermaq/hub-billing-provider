from hub_billing.adapters.base import (
    ProviderAccount,
    ProviderAdapter,
    ProviderCredentials,
    ProviderSession,
    RawBill,
)
from hub_billing.adapters.registry import register
from hub_billing.domain.models import UtilityType


@register("aegea")
class AegeaAdapter(ProviderAdapter):
    """Água e esgoto: unidades do grupo Aegea, incluindo Corsan (distributor="corsan").

    O meio de coleta será definido no épico E1; até lá os métodos de coleta não estão implementados.
    """

    utility_type = UtilityType.WATER

    async def authenticate(self, credentials: ProviderCredentials) -> ProviderSession:
        raise NotImplementedError("E1: definir meio de coleta da Aegea/Corsan")

    async def list_accounts(self, session: ProviderSession) -> list[ProviderAccount]:
        raise NotImplementedError("E1: definir meio de coleta da Aegea/Corsan")

    async def fetch_bills(
        self, session: ProviderSession, account: ProviderAccount, reference: str | None = None
    ) -> list[RawBill]:
        raise NotImplementedError("E1: definir meio de coleta da Aegea/Corsan")

    async def download_document(self, session: ProviderSession, bill: RawBill) -> bytes:
        raise NotImplementedError("E1: definir meio de coleta da Aegea/Corsan")
