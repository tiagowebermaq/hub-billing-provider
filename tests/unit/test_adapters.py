from datetime import date
from decimal import Decimal

import pytest

from hub_billing.adapters.base import ProviderAccount, ProviderCredentials, RawBill
from hub_billing.adapters.registry import available, get_adapter, load_builtin
from hub_billing.domain.models import UtilityType

load_builtin()


def test_builtin_adapters_registered() -> None:
    assert {"cpfl", "aegea", "fake"} <= set(available())


def test_unknown_provider() -> None:
    with pytest.raises(KeyError):
        get_adapter("nao-existe")


def test_cpfl_accepts_rge_and_rejects_unknown_distributor() -> None:
    assert get_adapter("cpfl", "rge").distributor == "rge"
    with pytest.raises(ValueError):
        get_adapter("cpfl", "outra")


@pytest.mark.parametrize(("code", "dist", "kind", "unit"), [
    ("cpfl", "rge", UtilityType.ENERGY, "kWh"), ("aegea", "corsan", UtilityType.WATER, "m3"),
])
def test_to_canonical(code: str, dist: str, kind: UtilityType, unit: str) -> None:
    raw = RawBill(provider=code, account_number="123", reference_month="2026-10", due_date=date(2026, 10, 15),
                  amount=Decimal("99.90"), payload={"digitable_line": "8360000", "consumption": "12"})
    b = get_adapter(code, dist).to_canonical(raw)
    assert (b.provider, b.distributor, b.utility_type, b.consumption_unit) == (code, dist, kind, unit)
    assert b.business_key == f"{code}:123:2026-10"
    assert b.digitable_line == "8360000"


async def test_fake_adapter_full_flow() -> None:
    adapter = get_adapter("fake")
    session = await adapter.authenticate(ProviderCredentials(login="u", secret="s"))
    accounts = await adapter.list_accounts(session)
    bills = await adapter.fetch_bills(session, accounts[0], "2026-09")
    assert bills[0].reference_month == "2026-09"
    assert (await adapter.download_document(session, bills[0])).startswith(b"%PDF")


async def test_real_adapters_pending_discovery() -> None:
    with pytest.raises(NotImplementedError):
        await get_adapter("aegea", "corsan").list_accounts(None)  # type: ignore[arg-type]
    with pytest.raises(NotImplementedError):
        await get_adapter("cpfl").fetch_bills(None, ProviderAccount(account_number="1"))  # type: ignore[arg-type]
