import pytest

from hub_billing.api.users import UserStore
from hub_billing.domain.permissions import Permission


def test_ensure_admin_only_creates_once() -> None:
    store = UserStore()
    first = store.ensure_admin("admin@x.com", "senha-admin")
    second = store.ensure_admin("outro@x.com", "outra")
    assert first is second
    assert first.must_change_password and first.is_admin
    assert first.permissions == set(Permission)


def test_email_is_case_insensitive_and_unique() -> None:
    store = UserStore()
    store.add("Ana@X.com", "senha-ana-123", "Ana", {"consulta"})
    assert store.by_email("ana@x.com") is not None
    with pytest.raises(ValueError):
        store.add("ana@x.com", "outra-senha", "Ana 2", set())


def test_inactive_user_cannot_authenticate() -> None:
    store = UserStore()
    user = store.add("bia@x.com", "senha-bia-123", "Bia", {"operador"})
    assert store.authenticate("bia@x.com", "senha-bia-123") is user
    assert store.authenticate("bia@x.com", "errada") is None
    user.active = False
    assert store.authenticate("bia@x.com", "senha-bia-123") is None
    assert store.authenticate("ninguem@x.com", "x") is None
