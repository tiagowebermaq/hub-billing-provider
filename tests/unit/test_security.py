import jwt
import pytest
from cryptography.fernet import Fernet

from hub_billing.core.security import SecretBox, create_token, decode_token, hash_password, verify_password


def test_password_hash_roundtrip() -> None:
    h = hash_password("s3nh@-forte")
    assert verify_password("s3nh@-forte", h)
    assert not verify_password("errada", h)


def test_token_roundtrip_and_expiry() -> None:
    claims = decode_token(create_token("u1", "chave-de-teste-com-32-bytes-ou-mais", 5), "chave-de-teste-com-32-bytes-ou-mais")
    assert claims["sub"] == "u1"
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_token(create_token("u1", "chave-de-teste-com-32-bytes-ou-mais", -1), "chave-de-teste-com-32-bytes-ou-mais")


def test_secret_box_encrypts_credentials() -> None:
    box = SecretBox(Fernet.generate_key().decode())
    token = box.encrypt("senha-do-portal")
    assert "senha-do-portal" not in token
    assert box.decrypt(token) == "senha-do-portal"
