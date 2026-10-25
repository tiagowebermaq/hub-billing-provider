import pytest

from hub_billing.messaging import topology
from hub_billing.workers import run


def test_every_worker_listens_to_a_declared_queue() -> None:
    assert set(run.WORKERS.values()) == {q.name for q in topology.MAIN_QUEUES}


def test_cli_rejects_unknown_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.argv", ["hub-worker", "inexistente"])
    with pytest.raises(SystemExit):
        run.main()
