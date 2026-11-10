"""Captura do SQL que uma sessão envia ao banco, para testes que contam ou inspecionam comandos."""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import event
from sqlalchemy.orm import Session


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
