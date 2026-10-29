import os
import threading

import pytest

from hub_billing.infra.repository import Repository

PG_URL = os.environ.get("HUB_TEST_PG_URL")


@pytest.mark.skipif(not PG_URL, reason="defina HUB_TEST_PG_URL com um PostgreSQL vazio para rodar")
def test_api_e_workers_criam_as_tabelas_ao_mesmo_tempo() -> None:
    erros: list[str] = []

    def sobe() -> None:
        try:
            Repository.from_url(str(PG_URL), "").create_schema()
        except Exception as exc:  # sem a trava, 'duplicate key ... pg_type_typname_nsp_index'
            erros.append(repr(exc))

    threads = [threading.Thread(target=sobe) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert erros == []


def test_schema_pode_ser_criado_de_novo() -> None:
    repo = Repository.from_url("sqlite+pysqlite:///:memory:", "")
    repo.create_schema()
    repo.create_schema()
    assert repo.list_runs() == []
