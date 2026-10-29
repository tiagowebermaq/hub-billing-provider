import pytest

from hub_billing.api.users import UserStore
from hub_billing.domain.permissions import Permission
from hub_billing.infra.repository import make_engine


def test_ensure_admin_only_creates_once() -> None:
    store = UserStore()
    first = store.ensure_admin("admin@x.com", "senha-admin")
    first.must_change_password = False
    store.save(first)
    second = store.ensure_admin("outro@x.com", "outra")
    assert second.id == first.id and second.email == "admin@x.com"
    assert second.is_admin
    assert second.permissions == set(Permission)
    assert len(store.all()) == 1


def test_env_admin_follows_dotenv_until_password_is_changed() -> None:
    store = UserStore()
    store.ensure_admin("admin@hub.local", "padrao")
    admin = store.ensure_admin("weber@x.com", "senha-do-env")  # .env editado e hub reiniciado
    assert admin.must_change_password
    assert store.authenticate("weber@x.com", "senha-do-env") is not None
    assert store.authenticate("admin@hub.local", "padrao") is None


def test_email_is_case_insensitive_and_unique() -> None:
    store = UserStore()
    store.add("Ana@X.com", "senha-ana-123", "Ana", {"consulta"})
    assert store.by_email("ana@x.com") is not None
    with pytest.raises(ValueError):
        store.add("ana@x.com", "outra-senha", "Ana 2", set())


def test_inactive_user_cannot_authenticate() -> None:
    store = UserStore()
    user = store.add("bia@x.com", "senha-bia-123", "Bia", {"operador"})
    assert store.authenticate("bia@x.com", "senha-bia-123") == user
    assert store.authenticate("bia@x.com", "errada") is None
    user.active = False
    store.save(user)
    assert store.authenticate("bia@x.com", "senha-bia-123") is None
    assert store.authenticate("ninguem@x.com", "x") is None


def test_users_survive_restart(tmp_path) -> None:  # type: ignore[no-untyped-def]
    url = f"sqlite:///{tmp_path / 'hub.db'}"
    store = UserStore(make_engine(url))
    admin = store.ensure_admin("admin@hub.local", "padrao")
    admin.must_change_password = False
    store.save(admin)
    ana = store.add("ana@x.com", "senha-ana-123", "Ana", {"consulta"})
    ana.extra_permissions = {Permission.BILLS_READ}
    store.save(ana)

    reiniciado = UserStore(make_engine(url))
    reiniciado.ensure_admin("admin@hub.local", "padrao")
    assert {u.email for u in reiniciado.all()} == {"admin@hub.local", "ana@x.com"}
    assert reiniciado.get(ana.id) == ana
    assert reiniciado.get(admin.id).must_change_password is False  # type: ignore[union-attr]


def test_reset_admin_volta_para_o_env(tmp_path) -> None:  # type: ignore[no-untyped-def]
    import sys
    from pathlib import Path

    from hub_billing.core.config import Settings

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
    import reset_admin

    url = f"sqlite:///{tmp_path / 'hub.db'}"
    store = UserStore(make_engine(url))
    assert store.reset_admin("admin@hub.local", "padrao").is_admin  # sem admin: cria
    admin = store.by_email("admin@hub.local")
    assert admin is not None
    admin.password_hash, admin.must_change_password, admin.active = "esquecida", False, False
    store.save(admin)

    settings = Settings(_env_file=None, admin_email="weber@x.com", admin_password="senha-do-env", database_url=url)
    assert reset_admin.reset(settings) == "weber@x.com"
    voltou = UserStore(make_engine(url)).authenticate("weber@x.com", "senha-do-env")
    assert voltou is not None and voltou.must_change_password and voltou.id == admin.id
