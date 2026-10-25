from collections import defaultdict
from collections.abc import Iterable

from hub_billing.domain.models import Bill, BillingBatch, BillStatus


class UnlinkedBillError(ValueError):
    """Conta sem imóvel vinculado: vai para revisão manual."""


def group_bills(bills: Iterable[Bill], correlation_id: str | None = None) -> list[BillingBatch]:
    """Agrupa contas por imóvel + contrato + competência, ignorando duplicadas pela chave de negócio."""
    seen: set[str] = set()
    groups: dict[tuple[str, str | None, str], list[Bill]] = defaultdict(list)
    for bill in bills:
        if bill.property_id is None:
            raise UnlinkedBillError(f"conta {bill.business_key} sem imóvel vinculado")
        if bill.business_key in seen:
            continue
        seen.add(bill.business_key)
        groups[(bill.property_id, bill.contract_id, bill.reference_month)].append(
            bill.model_copy(update={"status": BillStatus.GROUPED})
        )
    return [
        BillingBatch(property_id=p, contract_id=c, reference_month=m, bills=items, correlation_id=correlation_id)
        for (p, c, m), items in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][2]))
    ]
