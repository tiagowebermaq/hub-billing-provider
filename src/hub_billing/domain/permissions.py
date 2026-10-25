from enum import StrEnum


class Permission(StrEnum):
    USERS_MANAGE = "users:manage"
    ROLES_MANAGE = "roles:manage"
    CREDENTIALS_WRITE = "providers:credentials:write"
    PROPERTIES_WRITE = "properties:write"
    ACCOUNTS_WRITE = "accounts:write"
    COLLECTIONS_RUN = "collections:run"
    BATCHES_RESEND = "batches:resend"
    BILLS_READ = "bills:read"
    BATCHES_READ = "batches:read"
    WEBHOOKS_MANAGE = "webhooks:manage"
    API_CLIENTS_MANAGE = "api-clients:manage"
    AUDIT_READ = "audit:read"


ROLE_DEFAULTS: dict[str, frozenset[Permission]] = {
    "admin": frozenset(Permission),
    "operador": frozenset({
        Permission.PROPERTIES_WRITE, Permission.ACCOUNTS_WRITE, Permission.COLLECTIONS_RUN,
        Permission.BATCHES_RESEND, Permission.BILLS_READ, Permission.BATCHES_READ,
    }),
    "consulta": frozenset({Permission.BILLS_READ, Permission.BATCHES_READ}),
}


def effective_permissions(roles: set[str], extra: set[Permission], is_admin: bool) -> set[Permission]:
    """Permissões do perfil somadas às permissões avulsas concedidas pelo admin."""
    if is_admin:
        return set(Permission)
    perms: set[Permission] = set(extra)
    for role in roles:
        perms |= ROLE_DEFAULTS.get(role, frozenset())
    return perms
