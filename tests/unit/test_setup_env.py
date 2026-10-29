import sys
from pathlib import Path

from cryptography.fernet import Fernet

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import setup_env  # noqa: E402


def read(path: Path) -> dict[str, str]:
    return dict(line.split("=", 1) for line in path.read_text(encoding="utf-8").splitlines() if "=" in line)


def test_cria_env_com_segredos_e_nao_sobrescreve(tmp_path: Path) -> None:
    (tmp_path / ".env.example").write_text(
        "HUB_ENV=dev\nHUB_JWT_SECRET=troque-por-um-segredo-com-32-caracteres-ou-mais\n# comentario\nHUB_ENCRYPTION_KEY=\n",
        encoding="utf-8")
    assert setup_env.setup(tmp_path) == ["HUB_JWT_SECRET", "HUB_ENCRYPTION_KEY"]
    first = read(tmp_path / ".env")
    assert first["HUB_ENV"] == "dev" and len(first["HUB_JWT_SECRET"]) >= 32
    Fernet(first["HUB_ENCRYPTION_KEY"].encode())  # chave válida
    assert setup_env.setup(tmp_path) == []
    assert read(tmp_path / ".env") == first


def test_main(tmp_path: Path, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    (tmp_path / ".env.example").write_text("HUB_ENCRYPTION_KEY=\n", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["setup_env.py", str(tmp_path)])
    setup_env.main()
    assert "HUB_ENCRYPTION_KEY" in capsys.readouterr().out
