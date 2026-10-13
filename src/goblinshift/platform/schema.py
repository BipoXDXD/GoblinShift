"""Compatibilidade entre o schema do banco e o código em execução.

Durante um deploy, processos de versões diferentes convivem por alguns instantes, e as
migrações seguem o padrão expand/contract (docs/GoblinShiftPlan.md, 3.8): primeiro só
acréscimos compatíveis com o código antigo, remoções num deploy posterior.
"""

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection


def read_db_heads(conn: Connection) -> frozenset[str]:
    """Revisões aplicadas no banco; vazio se nenhuma migração rodou."""
    return frozenset(MigrationContext.configure(conn).get_current_heads())


def read_code_revisions(alembic_ini: str) -> tuple[frozenset[str], frozenset[str]]:
    """Devolve (heads do código, todas as revisões conhecidas pelo código)."""
    script = ScriptDirectory.from_config(Config(alembic_ini))
    heads = frozenset(script.get_heads())
    known = frozenset(rev.revision for rev in script.walk_revisions())
    return heads, known


def schema_is_ready(
    db_heads: frozenset[str],
    code_heads: frozenset[str],
    known_revisions: frozenset[str],
) -> bool:
    """Decide se esta versão do código pode atender requisições com o schema atual.

    Casos que a regra precisa cobrir:
    - banco sem nenhuma migração (`db_heads` vazio);
    - banco exatamente nos heads do código;
    - banco numa revisão que o código não conhece (uma migração mais nova já foi aplicada,
      e este processo é da versão anterior, ainda rodando durante o deploy);
    - banco numa revisão conhecida, porém mais antiga que os heads (migração pendente).
    """
    if not db_heads:
        return False
    # Uma revisão do banco que o código conhece, mas que não é head, é migração pendente.
    if (db_heads & known_revisions) - code_heads:
        return False
    # Tudo conhecido: o banco precisa estar exatamente nos heads (inclusive com vários ramos).
    if db_heads <= known_revisions:
        return db_heads == code_heads
    # Há revisões mais novas que este código: uma migração "expand" já foi aplicada, e o
    # código anterior a tolera por construção (3.8). Atender durante o deploy é o esperado.
    return True
