import pytest
from fastapi.testclient import TestClient

from goblinshift.entrypoints.api.app import create_app
from goblinshift.entrypoints.api.health import ReadinessCache
from goblinshift.platform.settings import Settings
from tests.conftest import run_migrations


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


class CountingCheck:
    def __init__(self, *results: bool) -> None:
        self.results = list(results)
        self.calls = 0

    def __call__(self) -> bool:
        self.calls += 1
        return self.results.pop(0)


def test_readyz_consulta_o_banco_so_uma_vez_dentro_de_um_segundo() -> None:
    clock, check = FakeClock(), CountingCheck(True)
    cache = ReadinessCache(check, clock=clock)

    first = cache.is_ready()
    clock.now += 0.999
    second = cache.is_ready()

    assert (first, second) == (True, True)
    assert check.calls == 1


def test_readyz_consulta_de_novo_quando_o_resultado_completa_um_segundo() -> None:
    clock, check = FakeClock(), CountingCheck(False, True)
    cache = ReadinessCache(check, clock=clock)

    first = cache.is_ready()
    clock.now += 1.0
    second = cache.is_ready()

    assert (first, second) == (False, True)
    assert check.calls == 2


def test_readyz_guarda_tambem_o_resultado_negativo() -> None:
    clock, check = FakeClock(), CountingCheck(False)
    cache = ReadinessCache(check, clock=clock)

    results = [cache.is_ready(), cache.is_ready()]

    assert results == [False, False]
    assert check.calls == 1


def test_readyz_nao_guarda_excecao_inesperada() -> None:
    clock = FakeClock()
    calls = 0

    def failing_then_ready() -> bool:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("falha inesperada")
        return True

    cache = ReadinessCache(failing_then_ready, clock=clock)

    with pytest.raises(RuntimeError):
        cache.is_ready()
    assert cache.is_ready() is True


def test_livez_responde_sem_banco() -> None:
    app = create_app(Settings(environment="test", db_port=1))
    with TestClient(app) as client:
        response = client.get("/livez")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readyz_sem_banco_responde_503_sem_detalhes() -> None:
    app = create_app(Settings(environment="test", db_port=1, db_connect_timeout_s=1))
    with TestClient(app) as client:
        response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json() == {"status": "not_ready"}


@pytest.mark.integration
def test_readyz_depende_das_migracoes(db_settings: Settings) -> None:
    clock = FakeClock()
    app = create_app(db_settings, readiness_clock=clock)
    with TestClient(app) as client:
        run_migrations(db_settings, "base")
        assert client.get("/readyz").status_code == 503

        run_migrations(db_settings, "head")
        clock.now += 1.0
        response = client.get("/readyz")
        assert response.status_code == 200
        assert response.json() == {"status": "ready"}
