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


def test_parse_merged_prs_handles_merge_and_squash(build_po: ModuleType) -> None:
    log = ("Merge pull request #12 from weber/vivo\x1fAdapter Vivo\n\x1e"
           "Corrige typo\x1f\x1e"
           "Adiciona webhooks (#11)\x1f\x1e")
    assert build_po.parse_merged_prs(log) == ["PR #11: Adiciona webhooks", "PR #12: Adapter Vivo"]


@pytest.mark.parametrize(("count", "bumped"), [(9, False), (10, True)])
def test_every_prs_only_bumps_after_threshold(build_po: ModuleType, monkeypatch: pytest.MonkeyPatch,
                                              count: int, bumped: bool) -> None:
    version_file = build_po.PO_DIR / "VERSION"
    version_file.write_text("1.2.0\n", encoding="utf-8")
    monkeypatch.setattr(build_po, "VERSION_FILE", version_file)
    monkeypatch.setattr(build_po, "last_po_tag", lambda: "po-v1.2.0")
    monkeypatch.setattr(build_po, "merged_prs_since", lambda tag: [f"PR #{i}: t{i}" for i in range(count)])
    built: list[str] = []
    monkeypatch.setattr(build_po, "build", lambda v: built.append(v) or build_po.PO_DIR / f"v{v}")
    monkeypatch.setattr("sys.argv", ["build_po.py", "--every-prs", "10"])
    build_po.main()
    assert built == (["1.3.0"] if bumped else [])
    assert version_file.read_text(encoding="utf-8").strip() == ("1.3.0" if bumped else "1.2.0")
    if bumped:
        assert build_po.parse_changelog()[0][3][1:] == [f"PR #{i}: t{i}" for i in range(10)]
