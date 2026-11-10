"""Padrões de SQL compartilhados pelos módulos que guardam dados no PostgreSQL."""

from typing import Any

from sqlalchemy import CTE, Select


def skip_locked_batch(query: Select[Any], limit: int, *, name: str = "chosen") -> CTE:
    """Até `limit` linhas de `query`, travadas com `FOR UPDATE SKIP LOCKED` e escolhidas uma vez.

    Para lotes de manutenção: quem chama filtra o `UPDATE`/`DELETE` pelas chaves da CTE. Ela é
    `MATERIALIZED` porque um `IN (subconsulta com LIMIT ... SKIP LOCKED)` depende do plano: se o
    subplano for reexecutado (nested loop), cada execução trava outras linhas e o lote passa do
    teto. A ordem, se houver, vem de `query`.
    """
    return (
        query.limit(limit).with_for_update(skip_locked=True).cte(name).prefix_with("MATERIALIZED")
    )
