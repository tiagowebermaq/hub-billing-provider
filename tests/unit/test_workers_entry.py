import pytest

from hub_billing.messaging import topology
from hub_billing.workers import run


def test_every_worker_listens_to_a_declared_queue() -> None:
    assert set(run.WORKERS.values()) == {q.name for q in topology.MAIN_QUEUES}


def test_cli_rejects_unknown_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.argv", ["hub-worker", "inexistente"])
    with pytest.raises(SystemExit):
        run.main()


def test_falha_ao_subir_vai_para_o_log(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    async def quebra(name: str) -> None:
        raise ConnectionError("rabbitmq fora do ar")

    monkeypatch.setattr(run, "_run", quebra)
    monkeypatch.setattr("sys.argv", ["hub-worker", "collector"])
    with pytest.raises(ConnectionError):
        run.main()
    assert "worker parou com erro" in caplog.text
