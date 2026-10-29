"""Leitura da página inicial da Agência Virtual nova da CPFL (cpfl.com.br/agencia-virtual).

O site é Drupal com a sessão no servidor: as páginas chegam prontas em HTML. A página inicial traz a conta atual da
unidade consumidora escolhida no login (valor, competência, vencimento, situação e código de barras).
"""
from __future__ import annotations

import html
import json
import re
from pathlib import Path
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
        **perfil_uc(page),
    }


def perfil_uc(page: str) -> dict[str, str | None]:
    """Endereço e classe da UC aberta (cabeçalho do usuário) e o PIX copia e cola da conta atual."""
    def span(cls: str) -> str | None:
        m = re.search(rf'class="{cls}"><span title="([^"]*)"', page)
        return html.unescape(m.group(1)).strip() or None if m else None

    pix = re.search(r'class="[^"]*\bcodigo-pix\b.*?class="hidden codigo">\s*([^<]+?)\s*<', page, re.S)
    return {"endereco": span("user-logged-in-info"), "classe": span("user-logged-in-role"),
            "pix": html.unescape(pix.group(1)) if pix and "br.gov.bcb.pix" in pix.group(1) else None}


def is_login_page(url: str, page: str) -> bool:
    """A sessão expirada manda para o login (B2C) ou para a escolha de perfil, em vez da página inicial."""
    return "/agencia-virtual/" not in url or "b2clogin" in page[:5000]


# Escolha da unidade consumidora -----------------------------------------------------------------------------
# O login abre a agência numa UC só. As outras ficam na página "Escolha a unidade consumidora", um formulário Drupal:
# ao escolher o titular (parceiro_negocio) a lista de UCs chega por AJAX, e o botão Acessar troca a UC da sessão.
SELECAO = f"{SITE}/agencia/area-cliente/selecionar-perfil-instalacao"
SELECAO_AJAX = f"{SELECAO}?ajax_form=1&_wrapper_format=drupal_ajax"


def _attrs(tag: str) -> dict[str, str]:
    return {k.lower(): html.unescape(v) for k, v in re.findall(r'([\w-]+)="([^"]*)"', tag)}


def _inputs(fragment: str) -> list[dict[str, str]]:
    return [_attrs(tag) for tag in re.findall(r"<input\b[^>]*>", fragment, re.I)]


def parse_selecao(page: str) -> dict[str, Any] | None:
    """Campos do formulário de escolha: build id, titular padrão e endereço da lista de titulares."""
    hidden = {i.get("name"): i.get("value", "") for i in _inputs(page) if i.get("type") == "hidden"}
    if hidden.get("form_id") != "cpflenergia_service_selecionar_perfil_instalacao_form" or not hidden.get("form_build_id"):
        return None
    pn = next((i for i in _inputs(page) if i.get("name") == "parceiro_negocio"), {})
    state: dict[str, str] = {}
    settings = re.search(r'data-drupal-selector="drupal-settings-json">(.*?)</script>', page, re.S)
    if settings:
        try:
            ajax_state = json.loads(settings.group(1)).get("ajaxPageState") or {}
            state = {f"ajax_page_state[{k}]": str(v) for k, v in ajax_state.items() if k in ("theme", "theme_token", "libraries")}
        except ValueError:
            pass
    return {"form_build_id": hidden["form_build_id"], "form_id": hidden["form_id"],
            "parceiro_negocio": pn.get("value") or pn.get("data-custom-select-default") or "",
            "titular": titular_nome(pn.get("data-custom-select-default-text")),
            "pns_url": pn.get("data-url"), "ajax_page_state": state}


def parse_pns(data: Any) -> dict[str, str | None]:
    """Titulares do login (código -> nome), na resposta do /api/gestao-acesso/pns (formato do select2)."""
    rows = data.get("results") if isinstance(data, dict) else data
    return {str(r["id"]): titular_nome(r.get("text")) for r in rows or [] if isinstance(r, dict) and r.get("id")}


def titular_nome(text: Any) -> str | None:
    """"00000000000 - NOME DO TITULAR" -> "NOME DO TITULAR"."""
    name = str(text or "").rpartition(" - ")[2].strip()
    return name or None


def parse_ajax(commands: Any) -> tuple[str, str | None]:
    """HTML inserido pelos comandos AJAX do Drupal e o form_build_id novo, quando o Drupal troca."""
    fragments, build_id = [], None
    for cmd in commands if isinstance(commands, list) else []:
        if not isinstance(cmd, dict):
            continue
        if cmd.get("command") == "update_build_id":
            build_id = cmd.get("new")
        elif isinstance(cmd.get("data"), str):
            fragments.append(cmd["data"])
    return "\n".join(fragments), build_id


def parse_ucs(fragment: str) -> list[dict[str, str]]:
    """Opções de UC da lista (campo de escolha e valor), sem repetir e sem os campos de filtro."""
    found: dict[tuple[str, str], dict[str, str]] = {}
    for i in _inputs(fragment):
        name, value = i.get("name", ""), i.get("value", "")
        if i.get("type") == "radio" and name and value and not name.startswith("filtro_"):
            found.setdefault((name, value), {"campo": name, "valor": value})
    return list(found.values())


DIAG_DIR = Path("logs") / "cpfl-paginas"


def save_diagnostic(name: str, content: str) -> None:
    """Guarda no PC (logs/, fora do Git) a página que não deu para ler, para ajustar a leitura depois."""
    try:
        DIAG_DIR.mkdir(parents=True, exist_ok=True)
        (DIAG_DIR / name).write_text(content, encoding="utf-8")
    except OSError:
        pass


def pdf_link(page: str) -> str | None:
    """Endereço do PDF quando a conta completa vem numa página (iframe, embed ou link) em vez do arquivo."""
    for m in re.finditer(r'<(?:iframe|embed|object|a)\b[^>]*?(?:src|data|href)="([^"]+)"', page, re.I):
        url = html.unescape(m.group(1))
        if "pdf" in url.lower() and not url.startswith(("javascript:", "#")):
            return url
    return None
