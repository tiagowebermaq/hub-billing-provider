"""Conversão dos formatos que os portais usam (datas, valores e competências) para o modelo do hub."""
from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any


def parse_date(value: Any) -> date:
    """Aceita 'dd/mm/aaaa', 'aaaa-mm-dd' e ISO com hora."""
    text = str(value).strip()
    m = re.match(r"^(\d{2})/(\d{2})/(\d{4})", text)
    if m:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError as exc:
        raise ValueError(f"data em formato desconhecido: {value!r}") from exc


def parse_money(value: Any) -> Decimal:
    """Aceita número, '1234.56', '1234,56' e '1.234,56'."""
    if isinstance(value, int | float | Decimal):
        return Decimal(str(value)).quantize(Decimal("0.01"))
    text = str(value).strip().replace("R$", "").replace(" ", "")
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    try:
        return Decimal(text).quantize(Decimal("0.01"))
    except InvalidOperation as exc:
        raise ValueError(f"valor em formato desconhecido: {value!r}") from exc


def parse_reference(value: Any) -> str:
    """'MM/AAAA' ou 'AAAA-MM' -> 'AAAA-MM'."""
    text = str(value).strip()
    m = re.match(r"^(\d{1,2})/(\d{4})$", text)
    if m:
        return f"{m.group(2)}-{int(m.group(1)):02d}"
    if re.match(r"^\d{4}-\d{2}$", text):
        return text
    raise ValueError(f"competência em formato desconhecido: {value!r}")
