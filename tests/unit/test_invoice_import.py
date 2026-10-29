from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from hub_billing.adapters.registry import get_adapter, load_builtin
from hub_billing.api.app import MemoryPublisher
from hub_billing.collection.invoice import InvoiceParseError, parse_text, pdf_text
from hub_billing.domain.models import UtilityType
from tests.unit.invoices import invoice_lines, make_barcode, make_line, make_pdf

load_builtin()


def test_parse_pdf_da_cpfl() -> None:
    pdf = make_pdf(invoice_lines(make_barcode("3", 15_000)))
    parsed = get_adapter("cpfl", "rge").parse_invoice(pdf=pdf)
    assert (parsed.amount, parsed.due_date, parsed.reference_month, parsed.account_number) == (
        Decimal("150.00"), date(2026, 11, 20), "2026-10", "40012345")
    assert parsed.warnings == []


def test_rotulos_por_concessionaria_e_acentos() -> None:
    corsan = get_adapter("aegea", "corsan").parse_invoice(text="\n".join(invoice_lines(make_barcode("2"), "Matrícula", "778899")))
    vivo = get_adapter("vivo").parse_invoice(text="\n".join(invoice_lines(make_barcode("4"), "Número da conta", "0999111")))
    assert (corsan.account_number, vivo.account_number) == ("778899", "0999111")
    assert corsan.warnings == vivo.warnings == []


def test_avisos_de_segmento_e_valor_divergente() -> None:
    text = "\n".join(invoice_lines(make_barcode("2", 15_000), total="149,00"))
    parsed = get_adapter("cpfl").parse_invoice(text=text)
    assert any("difere" in w for w in parsed.warnings)
    assert any("segmento 'saneamento'" in w for w in parsed.warnings)


def test_sem_linha_digitavel_usa_valor_impresso_e_competencia_por_extenso() -> None:
    parsed = parse_text("Vencimento: 05/12/2026\nOUT/2026\nValor total R$ 1.234,56")
    assert parsed.amount == Decimal("1234.56") and parsed.reference_month == "2026-10"
    assert parsed.barcode is None and "linha digitável não encontrada" in parsed.warnings


def test_raw_from_invoice_prioriza_dados_do_operador_e_exige_minimo() -> None:
    adapter = get_adapter("vivo")
    parsed = adapter.parse_invoice(text="\n".join(invoice_lines(make_barcode("4"), "Codigo do cliente", "555")))
    raw = adapter.raw_from_invoice(parsed, account_number="777", reference_month="2026-09")
    assert (raw.account_number, raw.reference_month, raw.payload["source"]) == ("777", "2026-09", "upload")
    bill = adapter.to_canonical(raw)
    assert bill.utility_type is UtilityType.TELECOM and bill.digitable_line and bill.barcode
    with pytest.raises(InvoiceParseError, match="vencimento"):
        adapter.raw_from_invoice(parse_text("nada aqui"), account_number="1", reference_month="2026-10")
    with pytest.raises(InvoiceParseError):
        adapter.parse_invoice()


def test_pdf_ilegivel() -> None:
    with pytest.raises(InvoiceParseError, match="PDF"):
        pdf_text(b"isto nao e pdf")


def test_api_importa_pdf_e_publica_collect_result(client: TestClient, admin_headers: dict[str, str],
                                                  publisher: MemoryPublisher) -> None:
    pdf = make_pdf(invoice_lines(make_barcode("3", 15_000)))
    resp = client.post("/api/v1/bills/import", headers={**admin_headers, "X-Correlation-ID": "cid-imp"},
                       data={"provider": "cpfl", "distributor": "rge"}, files={"file": ("fatura.pdf", pdf, "application/pdf")})
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["bill"]["amount"] == "150.00" and body["bill"]["distributor"] == "rge"
    exchange, key, env = publisher.sent[-1]
    assert (exchange, key, env.type, env.correlation_id) == ("hub.collect", "collect.result.cpfl", "collect.result", "cid-imp")


def test_api_importa_texto_e_rejeita_fatura_incompleta(client: TestClient, admin_headers: dict[str, str]) -> None:
    text = "\n".join(invoice_lines(make_barcode("2"), "Matricula", "123456"))
    url = "/api/v1/bills/import"
    ok = client.post(url, headers=admin_headers, data={"provider": "aegea", "distributor": "corsan", "text": text})
    assert ok.status_code == 202, ok.text
    bad = client.post("/api/v1/bills/import", headers=admin_headers, data={"provider": "aegea", "text": "sem dados"})
    assert bad.status_code == 422
    unknown = client.post("/api/v1/bills/import", headers=admin_headers, data={"provider": "xpto", "text": text})
    assert unknown.status_code == 422
    wrong_dist = client.post(url, headers=admin_headers, data={"provider": "cpfl", "distributor": "x", "text": text})
    assert wrong_dist.status_code == 422


def test_api_valida_linha_digitavel(client: TestClient, admin_headers: dict[str, str]) -> None:
    line = make_line(make_barcode("4", 8_990)).replace(" ", "").replace("-", "")
    resp = client.get(f"/api/v1/barcodes/{line}", headers=admin_headers)
    assert resp.status_code == 200 and resp.json()["amount"] == "89.90" and resp.json()["segment"] == "telecomunicações"
    assert client.get("/api/v1/barcodes/123", headers=admin_headers).status_code == 422


def test_le_cadastro_do_imovel_e_do_titular() -> None:
    lines = invoice_lines(make_barcode("3")) + [
        "Nome: Maria José da Silva", "CPF: ***.456.789-**", "Endereço: Rua das Flores, 123 - Centro",
        "Porto Alegre RS CEP 90010000", "Classe: Residencial Bifásico"]
    parsed = get_adapter("cpfl", "rge").parse_invoice(text="\n".join(lines))
    assert parsed.consumer == {"name": "Maria José da Silva", "document": "***.456.789-**"}
    assert parsed.property == {"address": "Rua das Flores, 123 - Centro", "zip_code": "90010-000", "category": "Residencial"}
    bill = get_adapter("cpfl", "rge").to_canonical(get_adapter("cpfl", "rge").raw_from_invoice(parsed))
    assert bill.consumer is not None and bill.consumer.document == "***.456.789-**"
    assert bill.property_info is not None and bill.property_info.zip_code == "90010-000"


def test_cadastro_ausente_e_codigo_do_cliente_nao_viram_nome() -> None:
    parsed = parse_text("\n".join(invoice_lines(make_barcode("2"), "Código do cliente", "778899")) + "\nCNPJ: 12.345.678/0001-90")
    assert parsed.consumer == {"document": "12.345.678/0001-90"}
    assert parse_text("\n".join(invoice_lines(make_barcode("2")))).property == {}
