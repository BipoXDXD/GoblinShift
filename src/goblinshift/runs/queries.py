"""Consultas de execuções do dono: estado, histórico e resultados (docs/GoblinShiftPlan.md, 1.6).

Toda consulta filtra por `owner_id` na própria query: execução de outra conta é indistinguível
de inexistente. Rodam na transação de quem chama.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import ColumnElement, Row, Select, and_, case, func, select, tuple_
from sqlalchemy.orm import Session

from goblinshift.files.states import ArtifactRole, Availability
from goblinshift.files.tables import artifact, upload
from goblinshift.processing.recipe import (
    ITEM_COUNT_NAME,
    MAX_ITEM_COUNTS,
    ItemNoticeCode,
    ItemRejectionCode,
    ItemWarningCode,
)
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
    # Nulos depois da retenção de 30 dias (1.7).
    parameters: dict[str, Any] | None
    preset_id: UUID | None
    # Quando o primeiro resultado ainda disponível vence; nulo sem resultado disponível.
    results_expire_at: datetime | None

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES


class ItemStatus(StrEnum):
    PENDING = "pending"
    OK = "ok"
    REJECTED = "rejected"
    DUPLICATE = "duplicate"


@dataclass(frozen=True)
class RunItem:
    """Desfecho de uma entrada, só com códigos do catálogo e contagens: nada do arquivo."""

    position: int
    upload_id: UUID
    # Do próprio upload, que é do dono; anulado pela retenção de 30 dias (1.7).
    original_name: str | None
    status: ItemStatus
    error_code: ItemRejectionCode | None
    warnings: tuple[ItemWarningCode, ...]
    notices: tuple[ItemNoticeCode, ...]
    counts: dict[str, int]
    duplicate_of: int | None


@dataclass(frozen=True)
class RunDetail:
    view: RunView
    # Contagens do relatório (`inputs`, `ok`, `rejected`, `duplicates`, `outputs`), na publicação.
    summary: dict[str, int] | None
    items: tuple[RunItem, ...]


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
    # Nome com que o download entrega o arquivo.
    file_name: str
    availability: Availability


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


def get_run_detail(session: Session, owner_id: UUID, run_id: UUID) -> RunDetail:
    """A execução com o desfecho de cada entrada, na ordem das entradas.

    O detalhe gravado passou pela coleta do worker; mesmo assim só sai daqui o que está nas listas
    fechadas (códigos e formato das contagens), para um dado fora delas nunca chegar à API.
    """
    row = session.execute(
        _select_views()
        .add_columns(run.c.summary)
        .where(run.c.owner_id == owner_id, run.c.id == run_id)
    ).one_or_none()
    if row is None:
        raise RunNotFoundError
    items = session.execute(
        select(
            run_input.c.position,
            run_input.c.upload_id,
            upload.c.original_name,
            run_input.c.outcome,
            run_input.c.error_code,
            run_input.c.details,
        )
        .join(
            upload,
            and_(upload.c.id == run_input.c.upload_id, upload.c.owner_id == run_input.c.owner_id),
        )
        .where(run_input.c.owner_id == owner_id, run_input.c.run_id == run_id)
        .order_by(run_input.c.position)
    ).all()
    return RunDetail(_view(row), _summary(row.summary), tuple(_item(item) for item in items))


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
            artifact.c.file_name,
            artifact.c.expires_at,
            artifact.c.deleted_at,
            (artifact.c.expires_at <= func.now()).label("expired"),
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
        ResultReference(
            row.artifact_id,
            row.logical_name,
            ArtifactRole(row.role),
            row.file_name,
            Availability.of(
                deleted_at=row.deleted_at, expires_at=row.expires_at, expired=row.expired
            ),
        )
        for row in rows
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
        run.c.parameters,
        run.c.preset_id,
        _results_expire_at().label("results_expire_at"),
        _physical_attempts().label("attempt"),
        _count_inputs(run_input.c.outcome.is_not(None)).label("processed"),
        _count_inputs().label("total"),
        _queue_ahead().label("queue_ahead"),
    )


def _results_expire_at() -> ColumnElement[datetime]:
    """Vencimento mais próximo entre os artefatos publicados que ninguém excluiu."""
    return (
        select(func.min(artifact.c.expires_at))
        .join(
            run_output,
            and_(
                run_output.c.artifact_id == artifact.c.id,
                run_output.c.owner_id == artifact.c.owner_id,
            ),
        )
        .where(run_output.c.run_id == run.c.id, artifact.c.deleted_at.is_(None))
        .scalar_subquery()
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
        parameters=row.parameters,
        preset_id=row.preset_id,
        results_expire_at=row.results_expire_at,
    )


_SUMMARY_KEYS = ("inputs", "ok", "rejected", "outputs")


def _summary(raw: object) -> dict[str, int] | None:
    """As contagens finais; durante a execução `summary` guarda só o progresso, e sai nulo."""
    if not isinstance(raw, dict) or not all(_is_count(raw.get(key)) for key in _SUMMARY_KEYS):
        return None
    duplicates = raw.get("duplicates", 0)
    return {
        **{key: raw[key] for key in _SUMMARY_KEYS},
        "duplicates": duplicates if _is_count(duplicates) else 0,
    }


def _item(row: Any) -> RunItem:
    details = row.details if isinstance(row.details, dict) else {}
    duplicate_of = details.get("duplicate_of")
    duplicate_of = duplicate_of if _is_count(duplicate_of) else None
    return RunItem(
        position=row.position,
        upload_id=row.upload_id,
        original_name=row.original_name,
        status=_item_status(row.outcome, duplicate_of),
        error_code=_catalog(ItemRejectionCode, row.error_code),
        warnings=_codes(ItemWarningCode, details.get("warnings")),
        notices=_codes(ItemNoticeCode, details.get("notices")),
        counts=_counts(details.get("counts")),
        duplicate_of=duplicate_of,
    )


def _item_status(outcome: str | None, duplicate_of: int | None) -> ItemStatus:
    if outcome is None:
        return ItemStatus.PENDING
    if outcome == "rejected":
        return ItemStatus.REJECTED
    return ItemStatus.OK if duplicate_of is None else ItemStatus.DUPLICATE


def _catalog[C: StrEnum](codes: type[C], raw: object) -> C | None:
    return codes(raw) if isinstance(raw, str) and raw in codes else None


def _codes[C: StrEnum](codes: type[C], raw: object) -> tuple[C, ...]:
    """Só os códigos da lista fechada, na ordem gravada; o resto é descartado."""
    return tuple(code for item in _as_list(raw) if (code := _catalog(codes, item)) is not None)


def _counts(raw: object) -> dict[str, int]:
    if not isinstance(raw, dict):
        return {}
    valid = {
        name: value
        for name, value in raw.items()
        if isinstance(name, str) and ITEM_COUNT_NAME.fullmatch(name) and _is_count(value)
    }
    return dict(list(valid.items())[:MAX_ITEM_COUNTS])


def _as_list(raw: object) -> list[object]:
    return raw if isinstance(raw, list) else []


def _is_count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _iso(moment: datetime | None) -> str | None:
    # Em UTC, para que o mesmo instante escrito com outro fuso não pareça outro filtro.
    return moment.astimezone(UTC).isoformat() if moment is not None else None
