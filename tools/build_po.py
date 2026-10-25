"""Gera o PO Hub Billing Provider (PDF) e o infográfico (PDF + PNG) versionados.

Uso (Windows ou Ubuntu):
    python tools/build_po.py                         # regera a versão atual (falha se já existir)
    python tools/build_po.py --bump minor -m "Adiciona provedor X" [-m "outro item"]
    python tools/build_po.py --bump patch -m "Corrige texto" --git   # também faz commit + tag

Requisitos: pip install playwright && python -m playwright install chromium
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PO_DIR = ROOT / "docs" / "po"
TEMPLATES = PO_DIR / "templates"
VERSION_FILE = PO_DIR / "VERSION"
CHANGELOG = PO_DIR / "CHANGELOG.md"
ENTRY_RE = re.compile(r"^## \[(?P<v>[^\]]+)\] - (?P<d>\S+) - (?P<a>.+)$")
MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
         "agosto", "setembro", "outubro", "novembro", "dezembro"]


def bump(version: str, part: str) -> str:
    major, minor, patch = (int(x) for x in version.split("."))
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def add_changelog_entry(version: str, author: str, items: list[str]) -> None:
    text = CHANGELOG.read_text(encoding="utf-8")
    entry = f"## [{version}] - {dt.date.today().isoformat()} - {author}\n" + "".join(f"- {i}\n" for i in items)
    idx = text.find("\n## [")
    text = text + "\n" + entry if idx == -1 else text[: idx + 1] + entry + "\n" + text[idx + 1 :]
    CHANGELOG.write_text(text, encoding="utf-8")


def parse_changelog() -> list[tuple[str, str, str, list[str]]]:
    entries: list[tuple[str, str, str, list[str]]] = []
    for line in CHANGELOG.read_text(encoding="utf-8").splitlines():
        m = ENTRY_RE.match(line.strip())
        if m:
            entries.append((m["v"], m["d"], m["a"], []))
        elif line.startswith("- ") and entries:
            entries[-1][3].append(line[2:].strip())
    return entries


def changelog_rows() -> str:
    rows = []
    for v, d, a, items in parse_changelog():
        lis = "".join(f"<li>{html.escape(i)}</li>" for i in items)
        rows.append(f"<tr><td>v{html.escape(v)}</td><td>{html.escape(d)}</td>"
                    f"<td>{html.escape(a)}</td><td><ul>{lis}</ul></td></tr>")
    return "\n".join(rows)


def render(template: str, version: str) -> str:
    today = dt.date.today()
    data_extenso = f"{today.day} de {MESES[today.month - 1]} de {today.year}"
    return (template.replace("{{VERSION}}", version)
                    .replace("{{DATE}}", data_extenso)
                    .replace("{{CHANGELOG_ROWS}}", changelog_rows()))


def build(version: str) -> Path:
    from playwright.sync_api import sync_playwright

    out = PO_DIR / f"v{version}"
    if out.exists():
        sys.exit(f"{out} já existe. Versões publicadas não são sobrescritas: use --bump.")
    out.mkdir(parents=True)
    po_html = out / f"PO_Hub_Billing_Provider_v{version}.html"
    info_html = out / f"Infografico_Hub_Billing_Provider_v{version}.html"
    po_html.write_text(render((TEMPLATES / "po.html").read_text(encoding="utf-8"), version), encoding="utf-8")
    info_html.write_text(render((TEMPLATES / "infografico.html").read_text(encoding="utf-8"), version), encoding="utf-8")

    launch = {"executable_path": os.environ["PO_CHROMIUM_PATH"]} if os.environ.get("PO_CHROMIUM_PATH") else {}
    footer = ('<div style="font-size:8px;width:100%;padding:0 16mm;color:#5b6675;display:flex;justify-content:space-between">'
              f'<span>PO Hub Billing Provider · v{version}</span>'
              '<span>Página <span class="pageNumber"></span> de <span class="totalPages"></span></span></div>')
    with sync_playwright() as p:
        browser = p.chromium.launch(**launch)
        page = browser.new_page()
        page.goto(po_html.as_uri())
        page.pdf(path=str(out / f"PO_Hub_Billing_Provider_v{version}.pdf"), format="A4", print_background=True,
                 display_header_footer=True, header_template="<div></div>", footer_template=footer,
                 prefer_css_page_size=True)
        page = browser.new_page(viewport={"width": 1588, "height": 1123}, device_scale_factor=2)
        page.goto(info_html.as_uri())
        page.screenshot(path=str(out / f"Infografico_Hub_Billing_Provider_v{version}.png"), full_page=True)
        page.pdf(path=str(out / f"Infografico_Hub_Billing_Provider_v{version}.pdf"), width="420mm", height="297mm",
                 print_background=True, prefer_css_page_size=True)
        browser.close()
    return out


def git_publish(version: str, out: Path) -> None:
    def run(*args: str) -> None:
        subprocess.run(["git", *args], cwd=ROOT, check=True)
    run("add", str(VERSION_FILE), str(CHANGELOG), str(out))
    run("commit", "-m", f"docs(po): PO Hub Billing Provider v{version}")
    run("tag", f"po-v{version}")
    run("push", "--follow-tags")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bump", choices=["major", "minor", "patch"])
    ap.add_argument("-m", "--message", action="append", default=[], help="item do changelog (repetível)")
    ap.add_argument("--author", default=os.environ.get("PO_AUTHOR", "Weber"))
    ap.add_argument("--git", action="store_true", help="commit, tag po-vX.Y.Z e push")
    args = ap.parse_args()

    version = VERSION_FILE.read_text(encoding="utf-8").strip()
    if args.bump:
        if not args.message:
            sys.exit("Informe ao menos um -m descrevendo a alteração.")
        version = bump(version, args.bump)
        add_changelog_entry(version, args.author, args.message)
        VERSION_FILE.write_text(version + "\n", encoding="utf-8")
    out = build(version)
    print(f"PO v{version} gerado em {out}")
    if args.git:
        git_publish(version, out)


if __name__ == "__main__":
    main()
