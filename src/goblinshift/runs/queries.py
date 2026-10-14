"""Consultas de execuções do dono: estado, histórico e resultados (docs/GoblinShiftPlan.md, 1.6).

Toda consulta filtra por `owner_id` na própria query: execução de outra conta é indistinguível
de inexistente. Rodam na transação de quem chama.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import ColumnElement, Row, Select, and_, case, func, select, tuple_
from sqlalchemy.orm import Session

from goblinshift.files.states import ArtifactRole
from goblinshift.files.tables import artifact
from goblinshift.runs.cursor import Position, decode_cursor, encode_cursor
from goblinshift.runs.lifecycle import TERMINAL_STATUSES, ErrorCode, Phase, Status
from goblinshift.runs.tables import attempt, run, run_input, run_output

DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100
# Estados em que a publicação já aconteceu (3.5): sucesso, ou falha por nenhuma saída válida,
# que publica o relatório sem saídas de conteúdo.
_PUBLISHED = (Status.SUCCEEDED, Status.SUCCEEDED_WITH_WARNINGS)


class RunNotFoundError(LookupError):
    """Inexistente ou de outra conta (→ 404)."""

    def __init__(self) -> None:
        super().__init__("execução não encontrada")


@dataclass(frozen=True)
class RunView:
    id: UUID
    recipe_id: str
    recipe_version: int
    status: Status
    phase: Phase | None
    # Tentativas físicas (o executor começou a trabalhar); informativo (1.6).
    attempt: int
    processed: int
    total: int
    queue_ahead: int
    created_at: datetime
    queue_expires_at: datetime
    deadline_at: datetime | None
    error_code: str | None

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES


@dataclass(frozen=True)
class RunFilters:
    recipe_id: str | None = None
    status: Status | None = None
    created_from: datetime | None = None  # inclusivo
    created_to: datetime | None = None  # exclusivo

    def cursor_scope(self) -> dict[str, str | None]:
        """Os filtros como o cursor os guarda, para recusar cursor de outra consulta."""
        return {
            "recipe_id": self.recipe_id,
            "status": self.status.value if self.status is not None else None,
            "created_from": _iso(self.created_from),
            "created_to": _iso(self.created_to),
        }


@dataclass(frozen=True)
class RunPage:
    items: tuple[RunView, ...]
    next_cursor: str | None


@dataclass(frozen=True)
class ResultReference:
    artifact_id: UUID
    logical_name: str
    role: ArtifactRole


@dataclass(frozen=True)
class RunResults:
    published: bool
    expires_at: datetime | None
    references: tuple[ResultReference, ...]


def get_run(session: Session, owner_id: UUID, run_id: UUID) -> RunView:
    row = session.execute(
        _select_views().where(run.c.owner_id == owner_id, run.c.id == run_id)
    ).one_or_none()
    if row is None:
        raise RunNotFoundError
    return _view(row)


def list_runs(
    session: Session,
    owner_id: UUID,
    filters: RunFilters,
    *,
    limit: int,
    cursor: str | None,
    secret: bytes,
    now: datetime,
) -> RunPage:
    """Mais recentes primeiro, por keyset `(created_at, id) <`; nunca por OFFSET (3.3).

    Lê um item a mais que `limit` para saber se há próxima página: o fim da lista é
    `next_cursor = None`, não uma página vazia. Cursor inválido levanta `InvalidCursorError`.
    """
    if not 1 <= limit <= MAX_PAGE_SIZE:
        raise ValueError(f"limit fora de [1, {MAX_PAGE_SIZE}]")
    scope = filters.cursor_scope()
    query = _select_views().where(run.c.owner_id == owner_id, *_filter_conditions(filters))
    if cursor is not None:
        after = decode_cursor(cursor, scope, secret=secret, now=now)
        query = query.where(tuple_(run.c.created_at, run.c.id) < (after.created_at, after.run_id))
    rows = session.execute(
        query.order_by(run.c.created_at.desc(), run.c.id.desc()).limit(limit + 1)
    ).all()
    items = tuple(_view(row) for row in rows[:limit])
    if len(rows) <= limit:
        return RunPage(items, None)
    last = items[-1]
    next_cursor = encode_cursor(Position(last.created_at, last.id), scope, secret=secret, now=now)
    return RunPage(items, next_cursor)


def get_results(session: Session, owner_id: UUID, run_id: UUID) -> RunResults:
    """`published` diz se a publicação já aconteceu, mesmo quando ela não teve saídas."""
    state = session.execute(
        select(run.c.status, run.c.error_code).where(run.c.owner_id == owner_id, run.c.id == run_id)
    ).one_or_none()
    if state is None:
        raise RunNotFoundError
    if not _was_published(Status(state.status), state.error_code):
        return RunResults(published=False, expires_at=None, references=())
    rows = session.execute(
        select(
            run_output.c.artifact_id,
            run_output.c.logical_name,
            artifact.c.role,
            artifact.c.expires_at,
        )
        .join(
            artifact,
            and_(
                artifact.c.id == run_output.c.artifact_id,
                artifact.c.owner_id == run_output.c.owner_id,
            ),
        )
        .where(run_output.c.owner_id == owner_id, run_output.c.run_id == run_id)
        .order_by(run_output.c.position)
    ).all()
    references = tuple(
        ResultReference(row.artifact_id, row.logical_name, ArtifactRole(row.role)) for row in rows
    )
    # Publicados na mesma transação, os artefatos vencem juntos; o mínimo é o prazo seguro.
    expires_at = min((row.expires_at for row in rows), default=None)
    return RunResults(published=True, expires_at=expires_at, references=references)


def _was_published(status: Status, error_code: str | None) -> bool:
    return status in _PUBLISHED or (
        status is Status.FAILED and error_code == ErrorCode.NO_VALID_OUTPUTS
    )


def _select_views() -> Select[Any]:
    return select(
        run.c.id,
        run.c.recipe_id,
        run.c.recipe_version,
        run.c.status,
        run.c.phase,
        run.c.created_at,
        run.c.queue_expires_at,
        run.c.deadline_at,
        run.c.error_code,
        _physical_attempts().label("attempt"),
        _count_inputs(run_input.c.outcome.is_not(None)).label("processed"),
        _count_inputs().label("total"),
        _queue_ahead().label("queue_ahead"),
    )


def _physical_attempts() -> ColumnElement[int]:
    return (
        select(func.count())
        .where(attempt.c.run_id == run.c.id, attempt.c.executor_started_at.is_not(None))
        .scalar_subquery()
    )


def _count_inputs(*conditions: ColumnElement[bool]) -> ColumnElement[int]:
    return select(func.count()).where(run_input.c.run_id == run.c.id, *conditions).scalar_subquery()


def _queue_ahead() -> ColumnElement[int]:
    """Execuções aceitas antes desta que ainda não começaram, de todas as contas (1.6).

    Só a contagem sai da consulta; nenhuma linha de outra conta chega a quem pergunta.
    """
    ahead = run.alias("ahead")
    earlier = (
        select(func.count())
        .where(
            ahead.c.status == Status.QUEUED,
            tuple_(ahead.c.created_at, ahead.c.id) < tuple_(run.c.created_at, run.c.id),
        )
        .scalar_subquery()
    )
    return case((run.c.status == Status.QUEUED, earlier), else_=0)


def _filter_conditions(filters: RunFilters) -> list[ColumnElement[bool]]:
    conditions: list[ColumnElement[bool]] = []
    if filters.recipe_id is not None:
        conditions.append(run.c.recipe_id == filters.recipe_id)
    if filters.status is not None:
        conditions.append(run.c.status == filters.status)
    if filters.created_from is not None:
        conditions.append(run.c.created_at >= filters.created_from)
    if filters.created_to is not None:
        conditions.append(run.c.created_at < filters.created_to)
    return conditions


def _view(row: Row[Any]) -> RunView:
    return RunView(
        id=row.id,
        recipe_id=row.recipe_id,
        recipe_version=row.recipe_version,
        status=Status(row.status),
        phase=Phase(row.phase) if row.phase is not None else None,
        attempt=row.attempt,
        processed=row.processed,
        total=row.total,
        queue_ahead=row.queue_ahead,
        created_at=row.created_at,
        queue_expires_at=row.queue_expires_at,
        deadline_at=row.deadline_at,
        error_code=row.error_code,
    )


def _iso(moment: datetime | None) -> str | None:
    # Em UTC, para que o mesmo instante escrito com outro fuso não pareça outro filtro.
    return moment.astimezone(UTC).isoformat() if moment is not None else None
