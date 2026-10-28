from decimal import Decimal

import pytest

from hub_billing.domain.barcode import BarcodeError, find_in_text, from_barcode, from_digitable_line, mod10, mod11
from hub_billing.domain.models import UtilityType
from tests.unit.invoices import make_barcode, make_line


def test_modulos() -> None:
    assert mod10("01230067896") == "3"
    assert mod11("01230067896") == "0"
    assert mod11("5") == "1"  # 5 x 2 = 10, resto 10 -> 11 - 10
    assert mod11("6") == "0"  # 6 x 2 = 12, resto 1 -> 0


@pytest.mark.parametrize("value_id", ["6", "8"])
def test_linha_digitavel_ida_e_volta(value_id: str) -> None:
    code = make_barcode(segment="3", amount_cents=12_345, value_id=value_id)
    parsed = from_digitable_line(make_line(code))
    assert parsed.barcode == code
    assert parsed.amount == Decimal("123.45")
    assert parsed.segment_name == "energia e gás"
    assert parsed.matches(UtilityType.ENERGY) and not parsed.matches(UtilityType.WATER)
    assert parsed.company_id == "0045"
    assert from_barcode(code).digitable_line == parsed.digitable_line


def test_quantidade_de_moeda_nao_tem_valor_em_reais() -> None:
    assert from_barcode(make_barcode(value_id="7")).amount is None


def test_segmento_6_usa_cnpj() -> None:
    assert from_barcode(make_barcode(segment="6", company="1234", free="5678" + "0" * 21)).company_id == "12345678"


@pytest.mark.parametrize("bad", ["", "1" * 48, "8" * 47])
def test_linha_com_tamanho_ou_produto_errado(bad: str) -> None:
    with pytest.raises(BarcodeError):
        from_digitable_line(bad)


def test_dv_de_bloco_errado() -> None:
    line = make_line(make_barcode()).replace(" ", "").replace("-", "")
    broken = line[:11] + str((int(line[11]) + 1) % 10) + line[12:]
    with pytest.raises(BarcodeError, match="bloco 1"):
        from_digitable_line(broken)


def test_dv_geral_errado_e_identificacao_invalida() -> None:
    code = make_barcode()
    with pytest.raises(BarcodeError, match="geral"):
        from_barcode(code[:3] + str((int(code[3]) + 1) % 10) + code[4:])
    with pytest.raises(BarcodeError, match="identificação"):
        from_barcode("835" + code[3:])
    with pytest.raises(BarcodeError):
        from_barcode("123")


def test_find_in_text_ignora_linhas_invalidas() -> None:
    good = make_barcode(amount_cents=999)
    bad = make_line(make_barcode())
    bad = bad[:-1] + str((int(bad[-1]) + 1) % 10)
    found = find_in_text(f"Pague até o vencimento\n{bad}\nou {make_line(good)}")
    assert found is not None and found.barcode == good
    assert find_in_text("sem código") is None
