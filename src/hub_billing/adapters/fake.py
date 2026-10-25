from datetime import date
from decimal import Decimal

from hub_billing.adapters.base import (
    ProviderAccount,
    ProviderAdapter,
    ProviderCredentials,
    ProviderSession,
    RawBill,
)
from hub_billing.adapters.registry import register
from hub_billing.domain.models import UtilityType


@register("fake")
class FakeAdapter(ProviderAdapter):
    """Provedor simulado para desenvolvimento local e testes ponta a ponta."""

    utility_type = UtilityType.ENERGY

    async def authenticate(self, credentials: ProviderCredentials) -> ProviderSession:
        return ProviderSession(provider=self.code, data={"user": credentials.login})

    async def list_accounts(self, session: ProviderSession) -> list[ProviderAccount]:
        return [ProviderAccount(account_number="0001"), ProviderAccount(account_number="0002")]

    async def fetch_bills(
        self, session: ProviderSession, account: ProviderAccount, reference: str | None = None
    ) -> list[RawBill]:
        ref = reference or "2026-10"
        year, month = (int(x) for x in ref.split("-"))
        return [RawBill(provider=self.code, account_number=account.account_number, reference_month=ref,
                        due_date=date(year, month, 20), amount=Decimal("123.45"),
                        payload={"consumption": Decimal("150"), "barcode": "8366000000"})]

    async def download_document(self, session: ProviderSession, bill: RawBill) -> bytes:
        return b"%PDF-1.4 fake"
