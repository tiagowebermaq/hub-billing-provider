from __future__ import annotations

import secrets
from collections.abc import Awaitable, Callable
from datetime import datetime
from importlib.resources import files
from typing import Annotated, Any, Protocol

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr

from hub_billing import __version__
from hub_billing.adapters.registry import available, get_adapter, load_builtin
from hub_billing.api.deps import current_user, get_app_settings, get_store, require
from hub_billing.api.users import User, UserStore
from hub_billing.collection.invoice import InvoiceParseError
from hub_billing.collection.portal_login import Capture, PortalLogins
from hub_billing.core import correlation
from hub_billing.core.config import Settings, get_settings
from hub_billing.core.security import create_token, hash_password, verify_password
from hub_billing.domain.barcode import BarcodeError, from_digitable_line
from hub_billing.domain.grouping import group_bills
from hub_billing.domain.models import BatchStatus, Bill, BillStatus, ConsumerInfo, PropertyInfo
from hub_billing.domain.permissions import ROLE_DEFAULTS, Permission
from hub_billing.infra.repository import Repository
from hub_billing.messaging import topology
from hub_billing.messaging.envelope import Envelope
from hub_billing.messaging.rabbit import RabbitPublisher

PAINEL_HTML = files("hub_billing.web").joinpath("painel.html").read_text(encoding="utf-8")


class Publisher(Protocol):
    async def __call__(self, exchange: str, routing_key: str, env: Envelope) -> None: ...


class MemoryPublisher:
    """Publisher dos testes: guarda as mensagens em memória. O hub de verdade usa create_production_app (RabbitMQ)."""

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
    must_change_password: bool
    roles: set[str]
    permissions: list[str]

    @classmethod
    def of(cls, u: User) -> UserOut:
        return cls(id=u.id, email=u.email, name=u.name, is_admin=u.is_admin, active=u.active,
                   must_change_password=u.must_change_password,
                   roles=u.roles, permissions=sorted(p.value for p in u.permissions))


class SignupIn(BaseModel):
    name: str
    email: EmailStr
    password: str


class StatusIn(BaseModel):
    active: bool


class TempPasswordOut(BaseModel):
    temporary_password: str


class PasswordIn(BaseModel):
    new_password: str


class PermissionsIn(BaseModel):
    roles: set[str] | None = None
    grant: set[Permission] = set()
    revoke: set[Permission] = set()


class AccountLinkIn(BaseModel):
    property_id: str
    contract_id: str | None = None
    property: PropertyInfo | None = None
    consumer: ConsumerInfo | None = None


class ProviderSessionIn(BaseModel):
    distributor: str | None = None
    data: dict[str, Any]
    expires_at: datetime | None = None


class ConnectIn(BaseModel):
    distributor: str | None = None


class LoginJobOut(BaseModel):
    id: str
    provider: str
    distributor: str | None
    status: str
    error: str | None = None


class CloseBatchesIn(BaseModel):
    reference_month: str


class CollectionIn(BaseModel):
    provider: str
    distributor: str | None = None
    account_number: str | None = None
    reference_month: str | None = None


def create_production_app() -> FastAPI:
    """Fábrica usada pelo uvicorn no aplicacao.bat/.sh e no Docker: a API publica no RabbitMQ, onde os workers ouvem."""
    settings = get_settings()
    return create_app(settings, RabbitPublisher(settings.rabbitmq_url))


def create_app(settings: Settings | None = None, publisher: Publisher | None = None,
               repo: Repository | None = None, portal_capture: Capture | None = None) -> FastAPI:
    settings = settings or get_settings()
    load_builtin()
    app = FastAPI(title="Hub Billing Provider", version=__version__)
    app.state.settings = settings
    app.state.publisher = publisher or MemoryPublisher()
    app.state.repo = repo or Repository.from_url(settings.database_url, settings.encryption_key)
    app.state.repo.create_schema()
    app.state.users = UserStore(app.state.repo.engine)
    app.state.users.ensure_admin(settings.admin_email, settings.admin_password)
    db_: Repository = app.state.repo
    app.state.portal_logins = PortalLogins(
        lambda provider, distributor, data: db_.save_session(
            provider, distributor, data,
            datetime.fromisoformat(data["expires_at"]) if data.get("expires_at") else None),
        portal_capture)
    db: Repository = app.state.repo

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

    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        return RedirectResponse("/painel")

    @app.get("/painel", include_in_schema=False)
    async def painel() -> HTMLResponse:
        """Painel de administração: página única que usa a própria API com o token do login."""
        return HTMLResponse(PAINEL_HTML)

    @app.post("/api/v1/auth/login", response_model=TokenOut)
    async def login(form: Annotated[OAuth2PasswordRequestForm, Depends()],
                    store: Annotated[UserStore, Depends(get_store)],
                    cfg: Annotated[Settings, Depends(get_app_settings)]) -> TokenOut:
        user = store.authenticate(form.username, form.password)
        if user is None:
            pending = store.by_email(form.username)
            if pending and not pending.active and verify_password(form.password, pending.password_hash):
                raise HTTPException(status.HTTP_403_FORBIDDEN, "cadastro aguardando aprovação do administrador")
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "credenciais inválidas")
        token = create_token(user.id, cfg.jwt_secret, cfg.jwt_expire_minutes, cfg.jwt_algorithm)
        return TokenOut(access_token=token, must_change_password=user.must_change_password)

    @app.post("/api/v1/auth/signup", status_code=201)
    async def signup(body: SignupIn, store: Annotated[UserStore, Depends(get_store)]) -> dict[str, str]:
        """Pedido de acesso: a conta nasce inativa e sem perfis, até o admin aprovar no painel (aba Usuários)."""
        if len(body.password) < 10:
            raise HTTPException(422, "a senha precisa de ao menos 10 caracteres")
        if not body.name.strip():
            raise HTTPException(422, "informe o seu nome")
        try:
            store.add(body.email, body.password, body.name.strip(), set(), active=False)
        except ValueError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        return {"detail": "cadastro enviado; aguarde a aprovação do administrador"}

    @app.get("/api/v1/me", response_model=UserOut)
    async def me(user: Annotated[User, Depends(current_user)]) -> UserOut:
        return UserOut.of(user)

    @app.post("/api/v1/me/password", status_code=204)
    async def change_password(body: PasswordIn, user: Annotated[User, Depends(current_user)],
                              store: Annotated[UserStore, Depends(get_store)]) -> None:
        if len(body.new_password) < 10:
            raise HTTPException(422, "a senha precisa de ao menos 10 caracteres")
        if verify_password(body.new_password, user.password_hash):
            raise HTTPException(422, "a nova senha precisa ser diferente da atual")
        user.password_hash = hash_password(body.new_password)
        user.must_change_password = False
        store.save(user)

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

    def managed_user(store: UserStore, user_id: str) -> User:
        user = store.get(user_id)
        if user is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "usuário não encontrado")
        if user.is_admin:
            raise HTTPException(status.HTTP_409_CONFLICT, "o admin é alterado pelo .env e pelo resetar-senha.bat")
        return user

    @app.patch("/api/v1/users/{user_id}/status", response_model=UserOut)
    async def set_status(user_id: str, body: StatusIn,
                         _: Annotated[User, Depends(require(Permission.USERS_MANAGE))],
                         store: Annotated[UserStore, Depends(get_store)]) -> UserOut:
        """Aprova (ativa) ou bloqueia um usuário."""
        user = managed_user(store, user_id)
        user.active = body.active
        store.save(user)
        return UserOut.of(user)

    @app.post("/api/v1/users/{user_id}/reset-password", response_model=TempPasswordOut)
    async def reset_password(user_id: str, _: Annotated[User, Depends(require(Permission.USERS_MANAGE))],
                             store: Annotated[UserStore, Depends(get_store)]) -> TempPasswordOut:
        """Gera uma senha temporária, mostrada uma vez ao admin; o usuário troca no próximo acesso."""
        user = managed_user(store, user_id)
        temporary = secrets.token_urlsafe(9)
        user.password_hash, user.must_change_password = hash_password(temporary), True
        store.save(user)
        return TempPasswordOut(temporary_password=temporary)

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
        store.save(user)
        return UserOut.of(user)

    @app.get("/api/v1/providers")
    async def providers(_: Annotated[User, Depends(require(Permission.BILLS_READ))]) -> list[str]:
        return [p for p in available() if p != "fake" or settings.env == "dev"]

    @app.put("/api/v1/providers/{provider}/session", status_code=204)
    async def save_provider_session(provider: str, body: ProviderSessionIn,
                                    _: Annotated[User, Depends(require(Permission.CREDENTIALS_WRITE))]) -> None:
        """Recebe os tokens do login assistido (tools/login_portal.py). Ficam cifrados; a senha nunca passa pelo hub."""
        if provider not in available():
            raise HTTPException(422, f"provedor desconhecido: {provider}")
        db.save_session(provider, body.distributor, body.data, body.expires_at)

    @app.post("/api/v1/providers/{provider}/connect", response_model=LoginJobOut, status_code=202)
    async def connect_provider(provider: str, body: ConnectIn,
                               _: Annotated[User, Depends(require(Permission.CREDENTIALS_WRITE))]) -> LoginJobOut:
        """Abre o portal num navegador no PC do hub para o usuário entrar; a sessão é salva cifrada ao concluir."""
        try:
            job = app.state.portal_logins.start(provider, body.distributor)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        return LoginJobOut(**vars(job))

    @app.get("/api/v1/providers/connect/{job_id}", response_model=LoginJobOut)
    async def connect_status(job_id: str,
                             _: Annotated[User, Depends(require(Permission.CREDENTIALS_WRITE))]) -> LoginJobOut:
        job = app.state.portal_logins.get(job_id)
        if job is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "login não encontrado")
        return LoginJobOut(**vars(job))

    @app.get("/api/v1/providers/sessions")
    async def provider_sessions(_: Annotated[User, Depends(require(Permission.CREDENTIALS_WRITE))]) -> list[dict[str, object]]:
        return db.list_sessions()

    @app.post("/api/v1/collections", status_code=202)
    async def start_collection(body: CollectionIn,
                               _: Annotated[User, Depends(require(Permission.COLLECTIONS_RUN))]) -> dict[str, str]:
        if body.provider not in available():
            raise HTTPException(422, f"provedor desconhecido: {body.provider}")
        env = Envelope(type="collect.request", producer="api", payload=body.model_dump())
        await app.state.publisher(topology.EX_COLLECT, f"collect.request.{body.provider}", env)
        return {"collection_id": env.message_id, "correlation_id": env.correlation_id}

    @app.get("/api/v1/collections")
    async def list_collections(_: Annotated[User, Depends(require(Permission.COLLECTIONS_RUN))]) -> list[dict[str, object]]:
        return db.list_runs()

    @app.post("/api/v1/bills/import", status_code=202)
    async def import_bill(
        _: Annotated[User, Depends(require(Permission.COLLECTIONS_RUN))],
        provider: Annotated[str, Form()],
        file: Annotated[UploadFile | None, File(description="Fatura em PDF")] = None,
        text: Annotated[str | None, Form(description="Texto da fatura, quando não houver PDF")] = None,
        distributor: Annotated[str | None, Form()] = None,
        account_number: Annotated[str | None, Form()] = None,
        reference_month: Annotated[str | None, Form(description="AAAA-MM")] = None,
    ) -> dict[str, object]:
        """Importa uma fatura (upload manual ou encaminhada por e-mail) e a envia para normalização."""
        if provider not in available():
            raise HTTPException(422, f"provedor desconhecido: {provider}")
        try:
            adapter = get_adapter(provider, distributor)
            pdf = await file.read() if file is not None else None
            parsed = adapter.parse_invoice(pdf=pdf, text=text)
            raw = adapter.raw_from_invoice(parsed, account_number, reference_month)
            bill = adapter.to_canonical(raw)
        except (InvoiceParseError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc
        env = Envelope(type="collect.result", producer="api.import",
                       payload={"raw": raw.model_dump(mode="json"), "distributor": distributor})
        await app.state.publisher(topology.EX_COLLECT, f"collect.result.{provider}", env)
        return {"collection_id": env.message_id, "correlation_id": env.correlation_id,
                "bill": bill.model_dump(mode="json"), "warnings": parsed.warnings}

    @app.put("/api/v1/accounts/{provider}/{account_number}")
    async def link_account(provider: str, account_number: str, body: AccountLinkIn,
                           _: Annotated[User, Depends(require(Permission.ACCOUNTS_WRITE))]) -> dict[str, object]:
        """Vincula a conta da concessionária (UC, matrícula, código do cliente) a um imóvel/contrato.

        `property` e `consumer` guardam o cadastro do imóvel e do titular; completam o que a fatura não trouxer.
        """
        if provider not in available():
            raise HTTPException(422, f"provedor desconhecido: {provider}")
        released = db.link_account(provider, account_number, body.property_id, body.contract_id,
                                   body.property, body.consumer)
        return {"provider": provider, "account_number": account_number, **body.model_dump(mode="json"),
                "bills_released": released}

    @app.get("/api/v1/accounts")
    async def list_accounts(_: Annotated[User, Depends(require(Permission.BILLS_READ))]) -> list[dict[str, object]]:
        return db.list_links()

    @app.get("/api/v1/bills", response_model=list[Bill])
    async def list_bills(_: Annotated[User, Depends(require(Permission.BILLS_READ))],
                         reference_month: str | None = None, status: BillStatus | None = None) -> list[Bill]:
        return db.list_bills(reference_month, status)

    @app.get("/api/v1/documents/{provider}/{account_number}/{reference_month}")
    async def get_document(provider: str, account_number: str, reference_month: str,
                           _: Annotated[User, Depends(require(Permission.BILLS_READ))]) -> Response:
        """PDF da conta baixado do portal na coleta."""
        doc = db.load_document(f"{provider}:{account_number}:{reference_month}")
        if doc is None:
            raise HTTPException(404, "PDF desta conta ainda não foi baixado: rode a coleta de novo")
        name = f"conta-{provider}-{account_number}-{reference_month}.pdf"
        return Response(doc[0], media_type=doc[1], headers={"Content-Disposition": f'inline; filename="{name}"'})

    @app.post("/api/v1/batches/close", status_code=202)
    async def close_batches(body: CloseBatchesIn,
                            _: Annotated[User, Depends(require(Permission.COLLECTIONS_RUN))]) -> dict[str, object]:
        """Fecha os lotes da competência (por imóvel/contrato) e os envia ao sistema de aluguéis."""
        ready = db.list_bills(body.reference_month, BillStatus.NORMALIZED)
        env = Envelope(type="batch.close", producer="api", payload=body.model_dump())
        created = group_bills(ready, env.correlation_id)
        for batch in created:
            db.save_batch(batch)
            out = env.derive("batch.ready", "api", {"batch_id": batch.id, "total": str(batch.total)})
            await app.state.publisher(topology.EX_BILLS, "batch.ready", out)
        pending = len(db.list_bills(body.reference_month, BillStatus.MANUAL_REVIEW))
        return {"correlation_id": env.correlation_id, "batches": [b.id for b in created],
                "bills": sum(len(b.bills) for b in created), "pending_manual_review": pending}

    @app.get("/api/v1/batches")
    async def list_batches(_: Annotated[User, Depends(require(Permission.BATCHES_READ))],
                           reference_month: str | None = None) -> list[dict[str, object]]:
        return db.list_batches(reference_month)

    @app.post("/api/v1/batches/{batch_id}/resend", status_code=202)
    async def resend_batch(batch_id: str,
                           _: Annotated[User, Depends(require(Permission.BATCHES_RESEND))]) -> dict[str, str]:
        batch = db.get_batch(batch_id)
        if batch is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "lote não encontrado")
        db.set_batch_status(batch_id, BatchStatus.READY)
        env = Envelope(type="batch.ready", producer="api.resend", payload={"batch_id": batch_id, "total": str(batch.total)})
        await app.state.publisher(topology.EX_BILLS, "batch.ready", env)
        return {"batch_id": batch_id, "correlation_id": env.correlation_id}

    @app.get("/api/v1/barcodes/{digitable_line}")
    async def check_barcode(digitable_line: str,
                            _: Annotated[User, Depends(require(Permission.BILLS_READ))]) -> dict[str, object]:
        """Valida uma linha digitável de conta de consumo e mostra valor e segmento."""
        try:
            code = from_digitable_line(digitable_line)
        except BarcodeError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"barcode": code.barcode, "digitable_line": code.digitable_line, "segment": code.segment_name,
                "amount": None if code.amount is None else str(code.amount), "company_id": code.company_id}

    return app

