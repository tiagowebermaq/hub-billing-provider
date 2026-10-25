from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated, Protocol

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr

from hub_billing import __version__
from hub_billing.adapters.registry import available, load_builtin
from hub_billing.api.deps import current_user, get_app_settings, get_store, require
from hub_billing.api.users import User, UserStore
from hub_billing.core import correlation
from hub_billing.core.config import Settings, get_settings
from hub_billing.core.security import create_token, hash_password
from hub_billing.domain.permissions import ROLE_DEFAULTS, Permission
from hub_billing.messaging import topology
from hub_billing.messaging.envelope import Envelope


class Publisher(Protocol):
    async def __call__(self, exchange: str, routing_key: str, env: Envelope) -> None: ...


class MemoryPublisher:
    """Publisher usado em desenvolvimento e testes; em produção é trocado pelo RabbitMQ."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, Envelope]] = []

    async def __call__(self, exchange: str, routing_key: str, env: Envelope) -> None:
        self.sent.append((exchange, routing_key, env))


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    must_change_password: bool = False


class UserIn(BaseModel):
    email: EmailStr
    name: str
    password: str
    roles: set[str] = set()


class UserOut(BaseModel):
    id: str
    email: str
    name: str
    is_admin: bool
    active: bool
    roles: set[str]
    permissions: list[str]

    @classmethod
    def of(cls, u: User) -> UserOut:
        return cls(id=u.id, email=u.email, name=u.name, is_admin=u.is_admin, active=u.active,
                   roles=u.roles, permissions=sorted(p.value for p in u.permissions))


class PasswordIn(BaseModel):
    new_password: str


class PermissionsIn(BaseModel):
    roles: set[str] | None = None
    grant: set[Permission] = set()
    revoke: set[Permission] = set()


class CollectionIn(BaseModel):
    provider: str
    distributor: str | None = None
    account_number: str | None = None
    reference_month: str | None = None


def create_app(settings: Settings | None = None, publisher: Publisher | None = None) -> FastAPI:
    settings = settings or get_settings()
    load_builtin()
    app = FastAPI(title="Hub Billing Provider", version=__version__)
    app.state.settings = settings
    app.state.users = UserStore()
    app.state.users.ensure_admin(settings.admin_email, settings.admin_password)
    app.state.publisher = publisher or MemoryPublisher()

    @app.middleware("http")
    async def correlation_middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        token = correlation.set(request.headers.get(correlation.HEADER) or correlation.new_id())
        try:
            response = await call_next(request)
            response.headers[correlation.HEADER] = correlation.ensure()
            return response
        finally:
            correlation.reset(token)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.post("/api/v1/auth/login", response_model=TokenOut)
    async def login(form: Annotated[OAuth2PasswordRequestForm, Depends()],
                    store: Annotated[UserStore, Depends(get_store)],
                    cfg: Annotated[Settings, Depends(get_app_settings)]) -> TokenOut:
        user = store.authenticate(form.username, form.password)
        if user is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "credenciais inválidas")
        token = create_token(user.id, cfg.jwt_secret, cfg.jwt_expire_minutes, cfg.jwt_algorithm)
        return TokenOut(access_token=token, must_change_password=user.must_change_password)

    @app.get("/api/v1/me", response_model=UserOut)
    async def me(user: Annotated[User, Depends(current_user)]) -> UserOut:
        return UserOut.of(user)

    @app.post("/api/v1/me/password", status_code=204)
    async def change_password(body: PasswordIn, user: Annotated[User, Depends(current_user)]) -> None:
        if len(body.new_password) < 10:
            raise HTTPException(422, "a senha precisa de ao menos 10 caracteres")
        user.password_hash = hash_password(body.new_password)
        user.must_change_password = False

    @app.get("/api/v1/users", response_model=list[UserOut])
    async def list_users(_: Annotated[User, Depends(require(Permission.USERS_MANAGE))],
                         store: Annotated[UserStore, Depends(get_store)]) -> list[UserOut]:
        return [UserOut.of(u) for u in store.all()]

    @app.post("/api/v1/users", response_model=UserOut, status_code=201)
    async def create_user(body: UserIn, _: Annotated[User, Depends(require(Permission.USERS_MANAGE))],
                          store: Annotated[UserStore, Depends(get_store)]) -> UserOut:
        unknown = body.roles - ROLE_DEFAULTS.keys()
        if unknown:
            raise HTTPException(422, f"perfis desconhecidos: {sorted(unknown)}")
        try:
            return UserOut.of(store.add(body.email, body.password, body.name, body.roles))
        except ValueError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    @app.patch("/api/v1/users/{user_id}/permissions", response_model=UserOut)
    async def set_permissions(user_id: str, body: PermissionsIn,
                              _: Annotated[User, Depends(require(Permission.USERS_MANAGE))],
                              store: Annotated[UserStore, Depends(get_store)]) -> UserOut:
        user = store.get(user_id)
        if user is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "usuário não encontrado")
        if user.is_admin:
            raise HTTPException(status.HTTP_409_CONFLICT, "as permissões do admin não podem ser alteradas")
        if body.roles is not None:
            user.roles = body.roles
        user.extra_permissions = (user.extra_permissions | body.grant) - body.revoke
        return UserOut.of(user)

    @app.get("/api/v1/providers")
    async def providers(_: Annotated[User, Depends(require(Permission.BILLS_READ))]) -> list[str]:
        return [p for p in available() if p != "fake" or settings.env == "dev"]

    @app.post("/api/v1/collections", status_code=202)
    async def start_collection(body: CollectionIn,
                               _: Annotated[User, Depends(require(Permission.COLLECTIONS_RUN))]) -> dict[str, str]:
        if body.provider not in available():
            raise HTTPException(422, f"provedor desconhecido: {body.provider}")
        env = Envelope(type="collect.request", producer="api", payload=body.model_dump())
        await app.state.publisher(topology.EX_COLLECT, f"collect.request.{body.provider}", env)
        return {"collection_id": env.message_id, "correlation_id": env.correlation_id}

    return app

