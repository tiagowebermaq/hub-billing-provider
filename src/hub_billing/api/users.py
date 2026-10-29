"""Usuários do hub, guardados no banco (PostgreSQL, ou SQLite nos testes) para sobreviverem a reinícios."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Engine, func, insert, select, update

from hub_billing.core.security import hash_password, verify_password
from hub_billing.domain.permissions import Permission, effective_permissions
from hub_billing.infra.repository import make_engine, metadata, users


@dataclass
class User:
    email: str
    password_hash: str
    name: str = ""
    is_admin: bool = False
    active: bool = True
    must_change_password: bool = False
    roles: set[str] = field(default_factory=set)
    extra_permissions: set[Permission] = field(default_factory=set)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))

    @property
    def permissions(self) -> set[Permission]:
        return effective_permissions(self.roles, self.extra_permissions, self.is_admin)


def _row(user: User) -> dict[str, Any]:
    return {"email": user.email.lower(), "password_hash": user.password_hash, "name": user.name,
            "is_admin": user.is_admin, "active": user.active, "must_change_password": user.must_change_password,
            "roles": sorted(user.roles), "extra_permissions": sorted(p.value for p in user.extra_permissions),
            "updated_at": datetime.now(UTC)}


def _user(row: Any) -> User:
    return User(id=row.id, email=row.email, password_hash=row.password_hash, name=row.name,
                is_admin=row.is_admin, active=row.active, must_change_password=row.must_change_password,
                roles=set(row.roles), extra_permissions={Permission(p) for p in row.extra_permissions})


class UserStore:
    def __init__(self, engine: Engine | None = None) -> None:
        self.engine = engine or make_engine("sqlite:///:memory:")
        metadata.create_all(self.engine, tables=[users])

    def ensure_admin(self, email: str, password: str) -> User:
        """Cria a conta admin no primeiro boot, com troca de senha obrigatória.

        Enquanto o admin não trocar a senha pelo painel, o e-mail e a senha do .env continuam valendo:
        editar o .env e reiniciar atualiza o acesso. Depois da troca, vale o que está no banco.
        """
        admin = self._one(users.c.is_admin.is_(True))
        if admin is None:
            admin = User(email=email, password_hash=hash_password(password), name="Administrador",
                         is_admin=True, must_change_password=True)
            with self.engine.begin() as conn:
                conn.execute(insert(users).values(id=admin.id, **_row(admin)))
        elif admin.must_change_password and not (
                admin.email == email.lower() and verify_password(password, admin.password_hash)):
            admin.email, admin.password_hash = email, hash_password(password)
            self.save(admin)
        return admin

    def reset_admin(self, email: str, password: str) -> User:
        """Volta o admin para o e-mail e a senha informados (os do .env), ativo e com troca de senha obrigatória."""
        admin = self._one(users.c.is_admin.is_(True))
        if admin is None:
            return self.ensure_admin(email, password)
        admin.email, admin.password_hash = email, hash_password(password)
        admin.active, admin.must_change_password = True, True
        self.save(admin)
        return admin

    def add(self, email: str, password: str, name: str, roles: set[str]) -> User:
        if self.by_email(email):
            raise ValueError("e-mail já cadastrado")
        user = User(email=email, password_hash=hash_password(password), name=name, roles=roles)
        with self.engine.begin() as conn:
            conn.execute(insert(users).values(id=user.id, **_row(user)))
        return user

    def save(self, user: User) -> None:
        """Grava as alterações feitas no usuário (senha, perfis, permissões, ativo)."""
        with self.engine.begin() as conn:
            conn.execute(update(users).where(users.c.id == user.id).values(**_row(user)))

    def get(self, user_id: str) -> User | None:
        return self._one(users.c.id == user_id)

    def by_email(self, email: str) -> User | None:
        return self._one(func.lower(users.c.email) == email.lower())

    def authenticate(self, email: str, password: str) -> User | None:
        user = self.by_email(email)
        if user and user.active and verify_password(password, user.password_hash):
            return user
        return None

    def all(self) -> list[User]:
        with self.engine.connect() as conn:
            return [_user(r) for r in conn.execute(select(users).order_by(users.c.email))]

    def _one(self, where: Any) -> User | None:
        with self.engine.connect() as conn:
            row = conn.execute(select(users).where(where)).first()
        return _user(row) if row else None
