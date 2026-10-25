from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from cryptography.fernet import Fernet

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    try:
        return _hasher.verify(hashed, password)
    except VerifyMismatchError:
        return False


def create_token(subject: str, secret: str, minutes: int, algorithm: str = "HS256", **claims: Any) -> str:
    now = datetime.now(UTC)
    payload = {"sub": subject, "iat": now, "exp": now + timedelta(minutes=minutes), **claims}
    return jwt.encode(payload, secret, algorithm=algorithm)


def decode_token(token: str, secret: str, algorithm: str = "HS256") -> dict[str, Any]:
    return jwt.decode(token, secret, algorithms=[algorithm])


class SecretBox:
    """Cifra credenciais de provedores (Fernet / AES-128-CBC + HMAC)."""

    def __init__(self, key: str) -> None:
        self._fernet = Fernet(key.encode() if key else Fernet.generate_key())

    def encrypt(self, plain: str) -> str:
        return self._fernet.encrypt(plain.encode()).decode()

    def decrypt(self, token: str) -> str:
        return self._fernet.decrypt(token.encode()).decode()
