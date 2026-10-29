"""Código de barras de arrecadação FEBRABAN (contas de consumo: energia, água, telecom).

Formato de 44 dígitos:
  1     produto (sempre 8 = arrecadação)
  2     segmento (2 saneamento, 3 energia e gás, 4 telecomunicações, ...)
  3     identificação do valor (6/7 = DV módulo 10, 8/9 = DV módulo 11; 6/8 = valor em reais)
  4     dígito verificador geral
  5-15  valor (centavos)
  16-19 identificação da empresa/órgão (segmento 6 usa CNPJ, posições 16-23)
  20-44 campo livre da empresa

A linha digitável tem 48 dígitos: 4 blocos de 11 dígitos do código de barras, cada um seguido do seu DV.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from hub_billing.domain.models import UtilityType

SEGMENTS = {"1": "prefeituras", "2": "saneamento", "3": "energia e gás", "4": "telecomunicações",
            "5": "órgãos governamentais", "6": "carnês e assemelhados", "7": "multas de trânsito", "9": "uso exclusivo do banco"}
UTILITY_SEGMENT = {UtilityType.WATER: "2", UtilityType.ENERGY: "3", UtilityType.TELECOM: "4"}
_REAL_VALUE = {"6", "8"}
_MOD10 = {"6", "7"}
_MOD11 = {"8", "9"}

_BLOCK = r"(\d{11})[\s.-]*(\d)"
DIGITABLE_LINE_RE = re.compile(r"(?<!\d)(?=8)" + r"[\s.-]+".join([_BLOCK] * 4) + r"(?!\d)")


class BarcodeError(ValueError):
    """Código de barras ou linha digitável inválidos."""


def mod10(digits: str) -> str:
    total = 0
    for i, d in enumerate(reversed(digits)):
        p = int(d) * (2 if i % 2 == 0 else 1)
        total += p // 10 + p % 10
    return str((10 - total % 10) % 10)


def mod11(digits: str) -> str:
    total = sum(int(d) * (2 + i % 8) for i, d in enumerate(reversed(digits)))
    rest = total % 11
    return "0" if rest in (0, 1) else str(11 - rest)


def _dv(value_id: str, digits: str) -> str:
    if value_id in _MOD10:
        return mod10(digits)
    if value_id in _MOD11:
        return mod11(digits)
    raise BarcodeError(f"identificação de valor inválida: {value_id}")


@dataclass(frozen=True)
class UtilityBarcode:
    barcode: str
    digitable_line: str

    @property
    def segment(self) -> str:
        return self.barcode[1]

    @property
    def segment_name(self) -> str:
        return SEGMENTS.get(self.segment, "desconhecido")

    @property
    def amount(self) -> Decimal | None:
        """Valor em reais, ou None quando o código traz quantidade de moeda/índice em vez de valor."""
        if self.barcode[2] not in _REAL_VALUE:
            return None
        return Decimal(int(self.barcode[4:15])).scaleb(-2)

    @property
    def company_id(self) -> str:
        return self.barcode[15:23] if self.segment == "6" else self.barcode[15:19]

    def matches(self, utility: UtilityType) -> bool:
        return UTILITY_SEGMENT.get(utility) == self.segment


def _only_digits(value: str) -> str:
    return re.sub(r"\D", "", value)


def from_barcode(value: str) -> UtilityBarcode:
    code = _only_digits(value)
    if len(code) != 44 or code[0] != "8":
        raise BarcodeError("código de arrecadação precisa ter 44 dígitos e começar com 8")
    if _dv(code[2], code[:3] + code[4:]) != code[3]:
        raise BarcodeError("dígito verificador geral inválido")
    blocks = [code[i:i + 11] for i in range(0, 44, 11)]
    line = "".join(b + _dv(code[2], b) for b in blocks)
    return UtilityBarcode(barcode=code, digitable_line=line)


def from_digitable_line(value: str) -> UtilityBarcode:
    line = _only_digits(value)
    if len(line) != 48 or line[0] != "8":
        raise BarcodeError("linha digitável de arrecadação precisa ter 48 dígitos e começar com 8")
    blocks = [line[i:i + 12] for i in range(0, 48, 12)]
    for n, block in enumerate(blocks, 1):
        if _dv(line[2], block[:11]) != block[11]:
            raise BarcodeError(f"dígito verificador do bloco {n} inválido")
    return from_barcode("".join(b[:11] for b in blocks))


def find_in_text(text: str) -> UtilityBarcode | None:
    """Primeira linha digitável válida encontrada no texto de uma fatura."""
    for m in DIGITABLE_LINE_RE.finditer(text):
        try:
            return from_digitable_line("".join(m.groups()))
        except BarcodeError:
            continue
    return None
