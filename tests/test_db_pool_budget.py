"""A soma dos pools do banco cabe no `max_connections` (ADR 073).

Cada processo abre o próprio engine: a API com o pool das settings (10 + 5, do qual o teto do
middleware deriva, ADR 050) e os outros com pools pequenos e nomeados em `platform/db.py`. A conta
do pico fica em `PEAK_CONNECTIONS`, a fonte da soma do ADR 073.
"""

import argparse
import json
import re
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml
from sqlalchemy import Engine

from goblinshift.entrypoints import admin_cli, maintenance, worker
from goblinshift.entrypoints.shutdown import ShutdownFlag
from goblinshift.platform.db import (
    ADMIN_POOL,
    MAINTENANCE_POOL,
    MIGRATE_POOL,
    WORKER_POOL,
    PoolSize,
)
from goblinshift.platform.settings import Settings
from goblinshift.runs.worker.runner import LocalRunner

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "compose.yaml"
COOLIFY_INFRA = ROOT / "deploy" / "coolify" / "infra.json"
# Padrão do PostgreSQL 18 e conexões reservadas ao superusuário (`superuser_reserved_connections`).
POSTGRES_DEFAULT_MAX_CONNECTIONS = 100
SUPERUSER_RESERVED = 3
_MAX_CONNECTIONS = re.compile(r"max_connections=(\d+)")


def _peak(pool: PoolSize) -> int:
    return pool.size + pool.overflow


def _api_pool() -> PoolSize:
    fields = Settings.model_fields
    return PoolSize(fields["db_pool_size"].default, fields["db_pool_max_overflow"].default)


# Pico de conexões por processo. A API conta duas vezes: no rollout a réplica nova sobe antes de a
# velha sair. O worker soma a conexão do `LISTEN`, aberta fora do pool. `healthchecks` são as do
# worker e da manutenção no `app.json` (`make_engine(settings)`, uma conexão cada); `backup` é o
# `pg_dump`.
PEAK_CONNECTIONS: dict[str, int] = {
    "api": 2 * _peak(_api_pool()),
    "worker": _peak(WORKER_POOL) + 1,
    "maintenance": _peak(MAINTENANCE_POOL),
    "admin": _peak(ADMIN_POOL),
    "migrate": _peak(MIGRATE_POOL),
    "healthchecks": 2,
    "backup": 1,
}


def _postgres_command(service: dict[str, Any]) -> str:
    command = service.get("command", "")
    return command if isinstance(command, str) else " ".join(command)


def postgres_max_connections() -> int:
    """`-c max_connections=` do serviço postgres no Compose e no Coolify; senão, o padrão."""
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]["postgres"]
    infra = json.loads(COOLIFY_INFRA.read_text(encoding="utf-8"))["services"]["postgres"]
    found = [
        int(match[1])
        for service in (compose, infra)
        if (match := _MAX_CONNECTIONS.search(_postgres_command(service)))
    ]
    return min(found, default=POSTGRES_DEFAULT_MAX_CONNECTIONS)


def test_soma_dos_pools_cabe_no_max_connections() -> None:
    budget = postgres_max_connections() - SUPERUSER_RESERVED

    assert sum(PEAK_CONNECTIONS.values()) <= budget, PEAK_CONNECTIONS


def test_api_fica_com_o_pool_das_settings() -> None:
    # O teto do middleware deriva deste pool (ADR 050): mudar aqui reabre aquele ADR.
    assert _api_pool() == PoolSize(10, 5)


class _EngineCapturedError(Exception):
    """Interrompe a composição logo depois de `make_engine`, sem abrir conexão."""


class _EngineSpy:
    def __init__(self) -> None:
        self.pools: list[PoolSize | None] = []

    def __call__(self, settings: Settings, *, pool: PoolSize | None = None) -> Engine:
        self.pools.append(pool)
        raise _EngineCapturedError


def _compose_worker() -> None:
    worker.serve(
        Settings(),
        LocalRunner(Path("unused"), attempt_timeout=timedelta(seconds=1)),
        ShutdownFlag(),
    )


def _compose_maintenance() -> None:
    maintenance.serve(
        Settings(),
        LocalRunner(Path("unused"), attempt_timeout=timedelta(seconds=1)),
        ShutdownFlag(),
    )


def _compose_admin_cli() -> None:
    args = argparse.Namespace(command=lambda _args, _context: 0)
    admin_cli._run_command(args, clock=lambda: 0.0)


@pytest.mark.parametrize(
    ("module", "compose", "expected"),
    [
        (worker, _compose_worker, WORKER_POOL),
        (maintenance, _compose_maintenance, MAINTENANCE_POOL),
        (admin_cli, _compose_admin_cli, ADMIN_POOL),
    ],
    ids=["worker", "maintenance", "admin_cli"],
)
def test_cada_processo_usa_o_proprio_pool(
    monkeypatch: pytest.MonkeyPatch, module: Any, compose: Any, expected: PoolSize
) -> None:
    spy = _EngineSpy()
    monkeypatch.setattr(module, "make_engine", spy)

    with pytest.raises(_EngineCapturedError):
        compose()

    assert spy.pools == [expected]


def test_migrate_usa_o_proprio_pool() -> None:
    # O `env.py` do Alembic só roda dentro de um comando do Alembic; os testes de migração o
    # exercitam contra o banco. Aqui, que ele pede o pool nomeado.
    source = (ROOT / "migrations" / "env.py").read_text(encoding="utf-8")

    assert "make_engine(settings, pool=MIGRATE_POOL)" in source
