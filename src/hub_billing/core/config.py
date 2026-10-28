from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuração lida de variáveis de ambiente com prefixo HUB_ (ou do arquivo .env)."""

    model_config = SettingsConfigDict(env_prefix="HUB_", env_file=".env", env_file_encoding="utf-8", extra="ignore")

    env: str = "dev"
    log_level: str = "INFO"
    jwt_secret: str = "dev-secret-change-me-before-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 30
    encryption_key: str = ""
    admin_email: str = "admin@hub.local"
    admin_password: str = "admin"
    rabbitmq_url: str = "amqp://hub:hub@localhost:5672/"
    redis_url: str = "redis://localhost:6379/0"
    database_url: str = "postgresql+psycopg://hub:hub@localhost:5432/hub"
    rental_api_url: str = "http://localhost:9000/api/bills"
    rental_api_secret: str = ""
    rental_timeout_seconds: float = 15.0
    aegea_apim_key: str = ""
    idempotency_ttl_seconds: int = 7 * 24 * 3600
    webhook_timeout_seconds: float = 10.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
