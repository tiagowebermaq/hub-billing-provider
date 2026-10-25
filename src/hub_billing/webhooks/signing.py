import hashlib
import hmac
import time

SIGNATURE_HEADER = "X-Hub-Signature-256"
TIMESTAMP_HEADER = "X-Hub-Timestamp"


def sign(secret: str, timestamp: int, body: bytes) -> str:
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256)
    return "sha256=" + mac.hexdigest()


def verify(secret: str, timestamp: int, body: bytes, signature: str, tolerance_s: int = 300,
           now: float | None = None) -> bool:
    """Para os consumidores: valida assinatura e rejeita replays fora da janela de tolerância."""
    if abs((now if now is not None else time.time()) - timestamp) > tolerance_s:
        return False
    return hmac.compare_digest(sign(secret, timestamp, body), signature)
