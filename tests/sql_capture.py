"""Captura do SQL que uma sessão envia ao banco, para testes que contam ou inspecionam comandos."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import cast

from sqlalchemy import ClauseElement, create_engine, event
from sqlalchemy.orm import Session

# Dialeto do driver da aplicação, só para compilar SQL: o engine nunca conecta.
POSTGRESQL = create_engine("postgresql+psycopg://").dialect


@contextmanager
def captured_sql(session: Session) -> Iterator[list[str]]:
    """SQL enviado ao banco pela sessão enquanto o bloco roda."""
    statements: list[str] = []

    def record(_conn: object, _cursor: object, sql: str, *_rest: object) -> None:
        statements.append(sql)

    engine = session.get_bind()
    event.listen(engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", record)


class _EmptyResult:
    """Resultado vazio que atende às leituras usadas pelos lotes de manutenção."""

    def all(self) -> list[object]:
        return []

    def scalars(self) -> _EmptyResult:
        return self

    def __iter__(self) -> Iterator[object]:
        return iter(())

    def scalar_one(self) -> int:
        return 0

    def scalar_one_or_none(self) -> None:
        return None


class RecordingSession:
    """Sessão sem banco: guarda cada comando e responde vazio, para inspecionar o SQL gerado."""

    def __init__(self) -> None:
        self.statements: list[ClauseElement] = []

    def execute(self, statement: ClauseElement, *_args: object, **_kwargs: object) -> _EmptyResult:
        self.statements.append(statement)
        return _EmptyResult()

    def as_session(self) -> Session:
        # Só `execute` é usado pelas funções sob teste; o resto da Session nunca é chamado.
        return cast(Session, self)

    def postgresql_sql(self) -> list[str]:
        return [str(s.compile(dialect=POSTGRESQL)) for s in self.statements]
