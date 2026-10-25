from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from hub_billing.domain.grouping import UnlinkedBillError, group_bills
from hub_billing.domain.models import Bill, BillStatus, UtilityType
from hub_billing.domain.permissions import Permission, effective_permissions


def bill(provider: str, account: str, amount: str, prop: str | None = "imovel-1", ref: str = "2026-10") -> Bill:
    return Bill(provider=provider, utility_type=UtilityType.ENERGY if provider == "cpfl" else UtilityType.WATER,
                account_number=account, property_id=prop, contract_id="ctr-1" if prop else None,
                reference_month=ref, due_date=date(2026, 10, 20), amount=Decimal(amount))


def test_reference_month_format() -> None:
    with pytest.raises(ValidationError):
        bill("cpfl", "1", "10", ref="2026-13")


def test_groups_energy_and_water_of_same_property() -> None:
    batches = group_bills([bill("cpfl", "uc1", "200.10"), bill("aegea", "mat1", "80.00"),
                           bill("cpfl", "uc2", "50", prop="imovel-2")], "cid")
    assert [b.property_id for b in batches] == ["imovel-1", "imovel-2"]
    assert batches[0].total == Decimal("280.10")
    assert all(b.status is BillStatus.GROUPED for b in batches[0].bills)
    assert batches[0].correlation_id == "cid"


def test_separates_reference_months_and_drops_duplicates() -> None:
    batches = group_bills([bill("cpfl", "uc1", "10"), bill("cpfl", "uc1", "10"),
                           bill("cpfl", "uc1", "12", ref="2026-11")])
    assert sorted((b.reference_month, len(b.bills)) for b in batches) == [("2026-10", 1), ("2026-11", 1)]


def test_unlinked_bill_is_rejected() -> None:
    with pytest.raises(UnlinkedBillError):
        group_bills([bill("cpfl", "uc1", "10", prop=None)])


def test_permissions_combine_roles_and_grants() -> None:
    perms = effective_permissions({"consulta"}, {Permission.COLLECTIONS_RUN}, is_admin=False)
    assert perms == {Permission.BILLS_READ, Permission.BATCHES_READ, Permission.COLLECTIONS_RUN}
    assert effective_permissions(set(), set(), is_admin=True) == set(Permission)
