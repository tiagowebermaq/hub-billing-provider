"""Repositório de usuários em memória. Será trocado por PostgreSQL no épico E2 mantendo a mesma interface."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from hub_billing.core.security import hash_password, verify_password
from hub_billing.domain.permissions import Permission, effective_permissions


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


class UserStore:
    def __init__(self) -> None:
        self._by_id: dict[str, User] = {}

    def ensure_admin(self, email: str, password: str) -> User:
        """Cria a conta admin no primeiro boot, com troca de senha obrigatória."""
        for user in self._by_id.values():
            if user.is_admin:
                return user
        admin = User(email=email, password_hash=hash_password(password), name="Administrador",
                     is_admin=True, must_change_password=True)
        self._by_id[admin.id] = admin
        return admin

    def add(self, email: str, password: str, name: str, roles: set[str]) -> User:
        if self.by_email(email):
            raise ValueError("e-mail já cadastrado")
        user = User(email=email, password_hash=hash_password(password), name=name, roles=roles)
        self._by_id[user.id] = user
        return user

    def get(self, user_id: str) -> User | None:
        return self._by_id.get(user_id)

    def by_email(self, email: str) -> User | None:
        return next((u for u in self._by_id.values() if u.email.lower() == email.lower()), None)

    def authenticate(self, email: str, password: str) -> User | None:
        user = self.by_email(email)
        if user and user.active and verify_password(password, user.password_hash):
            return user
        return None

    def all(self) -> list[User]:
        return list(self._by_id.values())
