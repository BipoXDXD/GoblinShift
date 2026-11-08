"""Regras puras dos limites por conta: override das cotas e comando de mudança (1.7)."""

from datetime import UTC, datetime
from typing import cast
from uuid import UUID

import pytest
from sqlalchemy.orm import Session

from goblinshift.runs.limits import change_account_limits
from goblinshift.runs.quotas import DailyUsage, QuotaLimits, QuotaOverride

DEFAULTS = QuotaLimits(runs_per_24h=30, input_bytes_per_24h=2000)
NOW = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
OWNER = UUID("01900000-0000-7000-8000-000000000001")
# Sem atributos: a recusa precisa acontecer antes de qualquer acesso ao banco.
NO_DATABASE = cast(Session, object())


@pytest.mark.parametrize(
    ("override", "expected"),
    [
        pytest.param(QuotaOverride(), QuotaLimits(30, 2000), id="sem-override"),
        pytest.param(QuotaOverride(runs_per_24h=1), QuotaLimits(1, 2000), id="so-execucoes"),
        pytest.param(QuotaOverride(input_bytes_per_24h=1), QuotaLimits(30, 1), id="so-bytes"),
        pytest.param(QuotaOverride(50, 9000), QuotaLimits(50, 9000), id="os-dois"),
    ],
)
def test_override_substitui_so_o_item_fixado(
    override: QuotaOverride, expected: QuotaLimits
) -> None:
    assert override.over(DEFAULTS) == expected


def test_uso_sem_execucoes_na_janela_nao_tem_data_de_reset() -> None:
    assert DailyUsage(runs=0, input_bytes=0, oldest_run_at=None).resets_at() is None


def test_cota_volta_24_horas_depois_da_execucao_mais_antiga() -> None:
    usage = DailyUsage(runs=1, input_bytes=10, oldest_run_at=NOW)

    assert usage.resets_at() == datetime(2026, 10, 14, 12, 0, tzinfo=UTC)


def test_mudanca_sem_nenhum_limite_e_recusada_antes_do_banco() -> None:
    with pytest.raises(ValueError, match="nenhum citado"):
        change_account_limits(NO_DATABASE, OWNER, {}, NOW)


def test_limite_desconhecido_e_recusado_antes_do_banco() -> None:
    with pytest.raises(ValueError, match="storage_gb"):
        change_account_limits(NO_DATABASE, OWNER, {"runs_per_24h": 5, "storage_gb": 1}, NOW)
