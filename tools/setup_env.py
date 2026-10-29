"""Cria o .env a partir do .env.example e gera os segredos que estiverem vazios ou com o valor de exemplo.

Nunca sobrescreve um segredo já preenchido: rodar de novo é seguro.
    python tools/setup_env.py
"""
from __future__ import annotations

import secrets
import sys
from pathlib import Path

from cryptography.fernet import Fernet

PLACEHOLDERS = {"", "troque-por-um-segredo-com-32-caracteres-ou-mais"}
GENERATORS = {
    "HUB_JWT_SECRET": lambda: secrets.token_urlsafe(48),
    "HUB_ENCRYPTION_KEY": lambda: Fernet.generate_key().decode(),
}


def setup(root: Path) -> list[str]:
    """Devolve os nomes das variáveis geradas."""
    env, example = root / ".env", root / ".env.example"
    lines = (env if env.exists() else example).read_text(encoding="utf-8").splitlines()
    generated = []
    for i, line in enumerate(lines):
        key, sep, value = line.partition("=")
        if sep and key in GENERATORS and value.strip() in PLACEHOLDERS:
            lines[i] = f"{key}={GENERATORS[key]()}"
            generated.append(key)
    env.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return generated


def main() -> None:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1]
    generated = setup(root)
    print("Segredos gerados no .env: " + ", ".join(generated) if generated else ".env já configurado.")


if __name__ == "__main__":
    main()
