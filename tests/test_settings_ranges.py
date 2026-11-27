"""Faixas e mensagens de erro de `Settings` (#442, itens 54 a e b).

Um valor recusado na subida não aparece na saída: o erro de validação nomeia o campo e o tipo,
nunca a entrada, que pode ser um segredo. Timeouts, intervalos e tamanhos de pool são finitos e
positivos: `0` desliga o timeout no PostgreSQL e `nan` faz o lease nunca vencer.
"""

import pytest
from pydantic import ValidationError

from goblinshift.platform.settings import Settings

CANARY = "CANARY-segredo-curto-na-variavel-de-ambiente"
SHORT_SECRET = CANARY[:20]

# Campo → é float (aceita `nan`/`inf` na leitura sem `allow_inf_nan=False`).
POSITIVE: dict[str, bool] = {
    "db_statement_timeout_ms": False,
    "db_lock_timeout_ms": False,
    "db_idle_in_transaction_timeout_ms": False,
    "db_connect_timeout_s": False,
    "db_pool_size": False,
    "db_pool_timeout_s": True,
    "worker_lease_s": True,
    "worker_poll_interval_s": True,
    "attempt_timeout_s": True,
    "maintenance_interval_s": True,
    "maintenance_statement_timeout_ms": False,
}
# Floats com faixa já existente que ganham a recusa de não finitos de graça (preflight F12).
OTHER_FLOATS = ("oauth_http_timeout_s", "missing_sweep_max_absent_ratio")
FLOATS = [name for name, is_float in POSITIVE.items() if is_float] + list(OTHER_FLOATS)
NON_FINITE = ["nan", "inf", "-inf", "1e309"]
PG_MAX_TIMEOUT_MS = 2_147_483_647
MS_FIELDS = (
    "db_statement_timeout_ms",
    "db_lock_timeout_ms",
    "db_idle_in_transaction_timeout_ms",
    "maintenance_statement_timeout_ms",
)


def _env(name: str) -> str:
    return "GS_" + name.upper()


def _rejected(monkeypatch: pytest.MonkeyPatch, field: str, value: str) -> ValidationError:
    monkeypatch.setenv(_env(field), value)
    with pytest.raises(ValidationError) as caught:
        Settings()
    return caught.value


@pytest.mark.parametrize("name", ["GS_CURSOR_SECRET", "GS_USER_HASH_SECRET"])
def test_segredo_curto_nao_aparece_no_erro(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    monkeypatch.setenv(name, SHORT_SECRET)
    with pytest.raises(ValidationError) as caught:
        Settings()
    assert SHORT_SECRET not in str(caught.value)
    assert SHORT_SECRET not in repr(caught.value)
    assert [error["type"] for error in caught.value.errors()] == ["too_short"]


@pytest.mark.parametrize("field", list(POSITIVE))
@pytest.mark.parametrize("value", ["0", "-1"])
def test_faixa_nao_positiva_e_recusada(
    monkeypatch: pytest.MonkeyPatch, field: str, value: str
) -> None:
    error = _rejected(monkeypatch, field, value)
    assert [e["loc"] for e in error.errors()] == [(field,)]
    assert [e["type"] for e in error.errors()] == ["greater_than"]
    assert "input_value" not in str(error)


def test_pool_sem_overflow_e_aceito_e_negativo_recusado(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GS_DB_POOL_MAX_OVERFLOW", "0")
    assert Settings().db_pool_max_overflow == 0
    error = _rejected(monkeypatch, "db_pool_max_overflow", "-1")
    assert [(e["loc"], e["type"]) for e in error.errors()] == [
        (("db_pool_max_overflow",), "greater_than_equal")
    ]


@pytest.mark.parametrize("field", FLOATS)
@pytest.mark.parametrize("value", NON_FINITE)
def test_float_nao_finito_e_recusado(
    monkeypatch: pytest.MonkeyPatch, field: str, value: str
) -> None:
    error = _rejected(monkeypatch, field, value)
    assert [e["loc"] for e in error.errors()] == [(field,)]
    assert [e["type"] for e in error.errors()] == ["finite_number"]
    # "inf" é substring de "information" no rodapé do pydantic: confere a ausência da entrada.
    assert "input_value" not in str(error)


@pytest.mark.parametrize("field", [name for name, is_float in POSITIVE.items() if not is_float])
@pytest.mark.parametrize("value", NON_FINITE)
def test_inteiro_nao_finito_e_recusado(
    monkeypatch: pytest.MonkeyPatch, field: str, value: str
) -> None:
    error = _rejected(monkeypatch, field, value)
    assert [e["loc"] for e in error.errors()] == [(field,)]
    assert "input_value" not in str(error)


def test_menor_valor_positivo_e_aceito(monkeypatch: pytest.MonkeyPatch) -> None:
    for field, is_float in POSITIVE.items():
        monkeypatch.setenv(_env(field), "0.001" if is_float else "1")
    settings = Settings()
    for field, is_float in POSITIVE.items():
        assert getattr(settings, field) == (0.001 if is_float else 1)


@pytest.mark.parametrize("value", ["0", "-1", "65536"])
def test_db_port_fora_da_faixa_e_recusada(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    error = _rejected(monkeypatch, "db_port", value)
    assert [e["loc"] for e in error.errors()] == [("db_port",)]
    assert "input_value" not in str(error)


@pytest.mark.parametrize("value", ["0", "-1"])
def test_executor_group_id_nao_positivo_e_recusado(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    error = _rejected(monkeypatch, "executor_group_id", value)
    assert [(e["loc"], e["type"]) for e in error.errors()] == [
        (("executor_group_id",), "greater_than")
    ]
    assert "input_value" not in str(error)


@pytest.mark.parametrize("field", MS_FIELDS)
def test_timeout_ms_acima_do_int32_do_postgres_e_recusado(
    monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    error = _rejected(monkeypatch, field, str(PG_MAX_TIMEOUT_MS + 1))
    assert [(e["loc"], e["type"]) for e in error.errors()] == [((field,), "less_than_equal")]
    assert "input_value" not in str(error)
