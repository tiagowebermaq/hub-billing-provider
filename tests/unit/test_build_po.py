"""Testa o gerador do PO versionado (sem abrir o navegador)."""
import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def build_po(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    spec = importlib.util.spec_from_file_location("build_po", ROOT / "tools" / "build_po.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    po = tmp_path / "docs" / "po"
    po.mkdir(parents=True)
    (po / "CHANGELOG.md").write_text("# Changelog\n\n## [1.0.0] - 2026-10-25 - Weber\n- Inicial\n", encoding="utf-8")
    monkeypatch.setattr(module, "PO_DIR", po)
    monkeypatch.setattr(module, "CHANGELOG", po / "CHANGELOG.md")
    return module


@pytest.mark.parametrize(("part", "expected"), [("major", "2.0.0"), ("minor", "1.3.0"), ("patch", "1.2.4")])
def test_bump(build_po: ModuleType, part: str, expected: str) -> None:
    assert build_po.bump("1.2.3", part) == expected


def test_new_entry_goes_on_top(build_po: ModuleType) -> None:
    build_po.add_changelog_entry("1.1.0", "Weber", ["Mais testes", "Docker <test>"])
    entries = build_po.parse_changelog()
    assert [e[0] for e in entries] == ["1.1.0", "1.0.0"]
    assert entries[0][3] == ["Mais testes", "Docker <test>"]


def test_render_fills_placeholders_and_escapes(build_po: ModuleType) -> None:
    build_po.add_changelog_entry("1.1.0", "Weber", ["Docker <test>"])
    out = build_po.render("v{{VERSION}} {{DATE}} <table>{{CHANGELOG_ROWS}}</table>", "1.1.0")
    assert out.startswith("v1.1.0 ")
    assert "{{" not in out
    assert "Docker &lt;test&gt;" in out


def test_refuses_to_overwrite_published_version(build_po: ModuleType) -> None:
    (build_po.PO_DIR / "v1.0.0").mkdir()
    pytest.importorskip("playwright")
    with pytest.raises(SystemExit):
        build_po.build("1.0.0")
