"""Leitura da página inicial da Agência Virtual nova da CPFL (cpfl.com.br/agencia-virtual).

O site é Drupal com a sessão no servidor: as páginas chegam prontas em HTML. A página inicial traz a conta atual da
unidade consumidora escolhida no login (valor, competência, vencimento, situação e código de barras).
"""
from __future__ import annotations

import html
import re
from typing import Any

SITE = "https://www.cpfl.com.br"
PAGINA_INICIAL = f"{SITE}/agencia-virtual/pagina-inicial"
CONTA_COMPLETA = f"{SITE}/agencia-virtual/conta-completa"
MESES = {m: i for i, m in enumerate(
    ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro",
     "dezembro"], start=1)}


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def parse_conta_atual(page: str) -> dict[str, Any] | None:
    """Card "Conta Atual" da página inicial. None quando a UC não tem conta (ou a página não é a da agência)."""
    card = re.search(r'class="[^"]*\bconta-atual\b.*?(?=class="[^"]*\bmodal-footer\b|$)', page, re.S)
    inst = re.search(r'data-instalacao="(\d+)"', page)
    if not card or not inst:
        return None
    body = card.group(0)
    valor = re.search(r'class="title">\s*R\$\s*([\d.,]+)', body)
    ref = re.search(r"Referente\s+(?:à|a)\s+(\w+)\s+de\s+(\d{4})", _text(body), re.I)
    venc = re.search(r"Vencimento:\s*(\d{2}/\d{2}/\d{4})\s*(?:<span>\s*([^<]+?)\s*</span>)?", body)
    if not (valor and ref and venc) or ref.group(1).lower() not in MESES:
        return None
    conta = re.search(r'data-numeroconta="(\d+)"', page)
    codigo = re.search(r'class="codigo">\s*([\d\s]{44,})<', body)
    return {
        "instalacao": inst.group(1),
        "numero_conta": conta.group(1) if conta else None,
        "valor": valor.group(1),
        "referencia": f"{ref.group(2)}-{MESES[ref.group(1).lower()]:02d}",
        "vencimento": venc.group(1),
        "situacao": (venc.group(2) or "").strip() or None,
        "codigo_barras": re.sub(r"\D", "", codigo.group(1)) if codigo else None,
    }


def is_login_page(url: str, page: str) -> bool:
    """A sessão expirada manda para o login (B2C) ou para a escolha de perfil, em vez da página inicial."""
    return "/agencia-virtual/" not in url or "b2clogin" in page[:5000]
