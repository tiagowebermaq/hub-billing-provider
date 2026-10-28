"""Leitura de faturas em PDF (upload manual ou e-mail de fatura), comum a todas as concessionárias.

Cada adapter informa os rótulos que a sua fatura usa para o número da conta; o resto (linha digitável,
vencimento e competência) segue o padrão das contas de consumo brasileiras.
"""
from __future__ import annotations

import io
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from pypdf import PdfReader

from hub_billing.domain.barcode import UtilityBarcode, find_in_text

MONTHS = {"JAN": 1, "FEV": 2, "MAR": 3, "ABR": 4, "MAI": 5, "JUN": 6, "JUL": 7, "AGO": 8, "SET": 9, "OUT": 10,
          "NOV": 11, "DEZ": 12}
_DATE = r"(\d{2})/(\d{2})/(\d{4})"
_DUE_RE = re.compile(r"vencimento\D{0,40}?" + _DATE, re.IGNORECASE)
_REF_NUM_RE = re.compile(r"(?:refer[êe]ncia|compet[êe]ncia|m[êe]s/ano|m[êe]s de refer[êe]ncia)\W{0,20}(\d{2})/(\d{4})",
                         re.IGNORECASE)
_REF_TXT_RE = re.compile(r"\b(" + "|".join(MONTHS) + r")[A-Z]*\s*[/ -]\s*(\d{4})\b", re.IGNORECASE)
_TOTAL_RE = re.compile(r"(?:total a pagar|valor a pagar|valor total|total)\D{0,20}?(\d{1,3}(?:\.\d{3})*,\d{2})", re.IGNORECASE)


class InvoiceParseError(ValueError):
    """Fatura sem dados mínimos (linha digitável, valor ou vencimento)."""


@dataclass
class ParsedInvoice:
    barcode: UtilityBarcode | None
    amount: Decimal | None
    due_date: date | None
    reference_month: str | None
    account_number: str | None
    warnings: list[str] = field(default_factory=list)


def pdf_text(data: bytes) -> str:
    try:
        reader = PdfReader(io.BytesIO(data))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception as exc:  # pypdf levanta vários tipos para PDFs corrompidos
        raise InvoiceParseError(f"PDF ilegível: {exc}") from exc


def strip_accents(value: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", value) if not unicodedata.combining(c))


def _brl(value: str) -> Decimal:
    return Decimal(value.replace(".", "").replace(",", "."))


def _account(text: str, labels: Iterable[str]) -> str | None:
    for label in labels:
        pattern = r"\b" + re.escape(strip_accents(label)) + r"\b\W{0,20}([0-9][0-9.\-/]{3,30}[0-9])"
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            return re.sub(r"\D", "", m.group(1))
    return None


def _reference(text: str) -> str | None:
    m = _REF_NUM_RE.search(text)
    if m:
        return f"{m.group(2)}-{m.group(1)}"
    m = _REF_TXT_RE.search(text)
    if m:
        return f"{m.group(2)}-{MONTHS[m.group(1)[:3].upper()]:02d}"
    return None


def parse_text(text: str, account_labels: Iterable[str] = ()) -> ParsedInvoice:
    text = strip_accents(text)
    barcode = find_in_text(text)
    warnings: list[str] = []
    amount = barcode.amount if barcode else None
    m = _TOTAL_RE.search(text)
    printed = _brl(m.group(1)) if m else None
    if amount is None:
        amount = printed
    elif printed is not None and printed != amount:
        warnings.append(f"valor impresso ({printed}) difere do código de barras ({amount})")
    due = _DUE_RE.search(text)
    due_date = date(int(due.group(3)), int(due.group(2)), int(due.group(1))) if due else None
    if barcode is None:
        warnings.append("linha digitável não encontrada")
    return ParsedInvoice(barcode=barcode, amount=amount, due_date=due_date, reference_month=_reference(text),
                         account_number=_account(text, account_labels), warnings=warnings)
