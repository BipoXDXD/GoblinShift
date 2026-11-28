"""Tempos e retry das migrations que disputam lock com a API (ADR 057).

Cada migration roda na sua transação com `lock_timeout` curto: se outra sessão segura a
tabela, a migration desiste em segundos em vez de enfileirar todo o tráfego atrás do seu pedido
de lock. As migrations já aplicadas ficam; a que desistiu é repetida algumas vezes com espera
crescente e sorteada. Esgotadas as tentativas, o comando falha com mensagem clara, o `migrate`
sai com erro e a API nova não sobe (ela depende do `migrate` concluído).
"""

import logging
import random
import time
from collections import deque
from collections.abc import Callable, Collection, Iterable, Iterator, Mapping
from functools import partial

from alembic.runtime.migration import MigrationContext, MigrationInfo, RevisionStep
from alembic.util import CommandError
from sqlalchemy.exc import OperationalError

from goblinshift.platform.db import is_lock_timeout

# Curto: enquanto a migration espera o lock, as consultas da API que chegam depois esperam
# atrás dela.
MIGRATION_LOCK_TIMEOUT_MS = 5_000
# Finito, mas acima do `statement_timeout` da API (5 s): um índice CONCURRENTLY ou um backfill
# em lote pode levar minutos.
MIGRATION_STATEMENT_TIMEOUT_MS = 15 * 60_000
LOCK_ATTEMPTS = 3
LOCK_RETRY_FIRST_DELAY_S = 1.0

log = logging.getLogger(__name__)


class RelativeMigrationPlan:
    """Retoma as etapas calculadas pelo Alembic para o destino relativo original."""

    def __init__(self) -> None:
        self._pending: deque[RevisionStep] | None = None
        self._confirmed_heads: frozenset[str] = frozenset()
        self._applied_heads: frozenset[str] = frozenset()

    def install(self, migration_context: MigrationContext) -> None:
        # O Alembic monta o fn do comando antes de carregar env.py e não expõe um getter.
        planner = migration_context._migrations_fn
        if planner is None:
            raise CommandError("o Alembic não forneceu um plano para a migration relativa")
        migration_context._migrations_fn = partial(self._remaining_steps, planner)

    def record_heads(
        self,
        ctx: MigrationContext,
        step: MigrationInfo,
        heads: Collection[str],
        run_args: Mapping[str, object],
    ) -> None:
        """Recebe o evento `on_version_apply` do Alembic antes do commit."""
        self._applied_heads = frozenset(heads)

    def _remaining_steps(
        self,
        planner: Callable[[tuple[str, ...], MigrationContext], Iterable[RevisionStep]],
        heads: tuple[str, ...],
        migration_context: MigrationContext,
    ) -> Iterator[RevisionStep]:
        current_heads = frozenset(heads)
        if self._pending is None:
            self._pending = deque(planner(heads, migration_context))
            self._confirmed_heads = current_heads
        elif current_heads != self._confirmed_heads:
            raise CommandError("o banco mudou entre tentativas da migration relativa")

        while self._pending:
            yield self._pending[0]
            # O callback roda dentro da transação. O Alembic só pede a próxima etapa após o commit.
            self._confirmed_heads = self._applied_heads
            self._pending.popleft()


def session_settings(lock_timeout_ms: int) -> list[str]:
    """Os SETs de sessão que valem para todas as migrations da conexão."""
    if lock_timeout_ms <= 0:
        # 0 desliga o limite no PostgreSQL: a migration esperaria o lock para sempre.
        raise ValueError(f"lock_timeout precisa ser positivo, veio {lock_timeout_ms}")
    return [
        f"SET lock_timeout = {lock_timeout_ms}",
        f"SET statement_timeout = {MIGRATION_STATEMENT_TIMEOUT_MS}",
    ]


def run_with_lock_retries(
    migrate: Callable[[], None],
    *,
    attempts: int = LOCK_ATTEMPTS,
    first_delay_s: float = LOCK_RETRY_FIRST_DELAY_S,
    jitter: Callable[[float], float] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Roda `migrate` e repete só quando ele desiste do lock; a espera dobra a cada tentativa."""
    if attempts <= 0:
        raise ValueError(f"attempts precisa ser positivo, veio {attempts}")
    draw = jitter or _equal_jitter
    for attempt in range(1, attempts + 1):
        try:
            migrate()
        except OperationalError as error:
            if not is_lock_timeout(error):
                raise
            if attempt == attempts:
                raise CommandError(
                    f"migration desistiu: lock ocupado em {attempts} tentativas; nenhuma "
                    "migration ficou segurando lock, e as anteriores a esta já estão aplicadas"
                ) from error
            delay_s = draw(first_delay_s * 2 ** (attempt - 1))
            log.warning(
                "migration sem o lock (tentativa %d de %d); nova tentativa em %.1f s",
                attempt,
                attempts,
                delay_s,
            )
            sleep(delay_s)
        else:
            return


def _equal_jitter(delay_s: float) -> float:
    """Metade fixa e metade sorteada: duas migrations não repetem no mesmo instante."""
    half = delay_s / 2
    return half + random.uniform(0, half)  # noqa: S311 - jitter, não segredo
