from hub_billing.adapters.base import ProviderAdapter

_REGISTRY: dict[str, type[ProviderAdapter]] = {}


def register(code: str):  # type: ignore[no-untyped-def]
    """Decorador: @register("cpfl") class CpflAdapter(ProviderAdapter): ..."""

    def wrap(cls: type[ProviderAdapter]) -> type[ProviderAdapter]:
        if code in _REGISTRY and _REGISTRY[code] is not cls:
            raise ValueError(f"adapter '{code}' já registrado")
        cls.code = code
        _REGISTRY[code] = cls
        return cls

    return wrap


def get_adapter(code: str, distributor: str | None = None) -> ProviderAdapter:
    try:
        cls = _REGISTRY[code]
    except KeyError as exc:
        raise KeyError(f"provedor desconhecido: {code}") from exc
    return cls(distributor)


def available() -> list[str]:
    return sorted(_REGISTRY)


def load_builtin() -> None:
    """Importa os adapters embarcados para que se registrem."""
    from hub_billing.adapters import aegea, cpfl, fake, vivo  # noqa: F401
