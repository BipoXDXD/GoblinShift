"""Retenção de arquivos para o processo `maintenance` (docs/GoblinShiftPlan.md, 1.7 e 3.4).

Cada função processa um lote numa transação própria e curta, travando com
`FOR UPDATE SKIP LOCKED` para não esperar por API ou worker. O laço que as repete é da fatia da
manutenção.

Ordem entre banco e disco: o estado é confirmado no banco primeiro e o arquivo é apagado depois
do commit. Se o processo cair no meio, sobra um arquivo órfão, que só ocupa espaço; a ordem
inversa poderia deixar uma linha apontando para um arquivo que já não existe caso o commit
falhasse, e quem confiasse na linha leria um objeto sumido. As falhas de remoção vão para o
relatório, não são engolidas.
"""

import uuid
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import (
    ColumnElement,
    Row,
    delete,
    exists,
    func,
    or_,
    select,
    union_all,
    update,
)
from sqlalchemy.orm import Session

from goblinshift import capacity
from goblinshift.files.limits import EXPIRY_BATCH_SIZE, PURGE_MAX_BYTES, PURGE_MAX_OBJECTS
from goblinshift.files.objects import (
    has_valid_reference,
    request_deletion_of_unreferenced,
    select_purgeable,
)
from goblinshift.files.states import ObjectState, UploadState
from goblinshift.files.storage import FileStorage
from goblinshift.files.tables import artifact, object_use, stored_object, upload

# Retenção longa (1.7): o nome do upload sai 30 dias depois da criação (só depois da exclusão) e
# as linhas de arquivos já removidos do disco, aos 90, junto com o histórico das execuções.
NAME_AFTER = timedelta(days=30)
FILE_ROWS_AFTER = timedelta(days=90)
_GONE_FROM_DISK = (ObjectState.DELETED, ObjectState.MISSING)

# (id, storage_key, size_bytes), como devolvido por `select_purgeable`.
type _Candidate = Row[tuple[uuid.UUID, str, int]]


@dataclass(frozen=True)
class PurgeReport:
    objects: int
    bytes: int
    # Havia mais objetos a remover do que o limitador permite num ciclo: a manutenção alerta.
    limit_reached: bool
    # Linhas já `deleted` cujo arquivo não saiu do disco (órfãos a investigar).
    failed_keys: tuple[str, ...]


def expire_incomplete_uploads(session: Session, storage: FileStorage) -> int:
    """Remove linha e objeto de uploads incompletos vencidos ou excluídos (1.7: "1 hora").

    Upload incompleto nunca chegou a uma execução nem tem conteúdo confirmado, então não há
    histórico a preservar. Um escritor ainda transferindo perde a vez na conclusão condicional e
    apaga o próprio arquivo. A reserva da intenção é liberada na mesma transação.
    """
    with session.begin():
        rows = session.execute(
            select(upload.c.id, upload.c.object_id, stored_object.c.storage_key)
            .join(stored_object, stored_object.c.id == upload.c.object_id)
            .where(
                upload.c.state != UploadState.READY,
                or_(upload.c.expires_at <= func.now(), upload.c.deleted_at.is_not(None)),
            )
            .order_by(upload.c.expires_at, upload.c.id)
            .limit(EXPIRY_BATCH_SIZE)
            .with_for_update(skip_locked=True, of=upload)
        ).all()
        if rows:
            upload_ids = [r.id for r in rows]
            session.execute(delete(upload).where(upload.c.id.in_(upload_ids)))
            capacity.release_many(session, holder=capacity.HolderKind.UPLOAD, holder_ids=upload_ids)
            session.execute(
                delete(stored_object).where(
                    stored_object.c.id.in_([r.object_id for r in rows]),
                    stored_object.c.state == ObjectState.PENDING,
                )
            )
    for row in rows:
        storage.delete(row.storage_key)
    return len(rows)


def expire_unused_uploads(session: Session) -> int:
    """Exclui uploads prontos vencidos sem uso aberto (1.7: "24 horas").

    Upload em uso por uma execução segue válido; o `release` da execução o estende para 48 h
    depois do término (`objects.release`), e só então volta a valer o prazo.
    A linha permanece com `deleted_at`, porque o histórico das execuções a referencia.
    """
    open_use = exists().where(
        object_use.c.object_id == upload.c.object_id, object_use.c.ended_at.is_(None)
    )
    with session.begin():
        rows = session.execute(
            select(upload.c.id, upload.c.object_id)
            .where(
                upload.c.state == UploadState.READY,
                upload.c.deleted_at.is_(None),
                upload.c.expires_at <= func.now(),
                ~open_use,
            )
            .order_by(upload.c.expires_at, upload.c.id)
            .limit(EXPIRY_BATCH_SIZE)
            .with_for_update(skip_locked=True)
        ).all()
        if rows:
            session.execute(
                update(upload)
                .where(upload.c.id.in_([r.id for r in rows]))
                .values(deleted_at=func.now())
            )
            request_deletion_of_unreferenced(session, [r.object_id for r in rows])
    return len(rows)


def expire_artifacts(session: Session) -> int:
    """Exclui artefatos vencidos (1.7: "48 horas"); downloads abertos seguram só o arquivo."""
    with session.begin():
        rows = session.execute(
            select(artifact.c.id, artifact.c.object_id)
            .where(artifact.c.deleted_at.is_(None), artifact.c.expires_at <= func.now())
            .order_by(artifact.c.expires_at, artifact.c.id)
            .limit(EXPIRY_BATCH_SIZE)
            .with_for_update(skip_locked=True)
        ).all()
        if rows:
            session.execute(
                update(artifact)
                .where(artifact.c.id.in_([r.id for r in rows]))
                .values(deleted_at=func.now())
            )
            request_deletion_of_unreferenced(session, [r.object_id for r in rows])
    return len(rows)


def purge_deleted_objects(session: Session, storage: FileStorage) -> PurgeReport:
    """Remove fisicamente objetos pedidos para exclusão, sem uso nem referência válida.

    Limitador de 3.4: no máximo `PURGE_MAX_OBJECTS` objetos ou `PURGE_MAX_BYTES` por ciclo.
    Busca um candidato a mais para saber se o limite cortou o lote. A reserva de cada objeto
    removido é liberada na mesma transação que o marca `deleted`.
    """
    with session.begin():
        candidates = session.execute(select_purgeable(limit=PURGE_MAX_OBJECTS + 1)).all()
        chosen = _within_governor(candidates)
        if chosen:
            object_ids = [c.id for c in chosen]
            session.execute(
                update(stored_object)
                .where(stored_object.c.id.in_(object_ids))
                .values(state=ObjectState.DELETED)
            )
            capacity.release_many(session, holder=capacity.HolderKind.OBJECT, holder_ids=object_ids)
    failed_keys = []
    for candidate in chosen:
        try:
            storage.delete(candidate.storage_key)
        except OSError:
            failed_keys.append(candidate.storage_key)
    return PurgeReport(
        objects=len(chosen),
        bytes=sum(c.size_bytes for c in chosen),
        limit_reached=len(chosen) < len(candidates),
        failed_keys=tuple(failed_keys),
    )


def anonymize_deleted_uploads(session: Session, now: datetime, limit: int) -> int:
    """Anula o nome dos uploads excluídos criados há 30 dias ou mais, sem uso aberto.

    Upload vivo, reaproveitado por execuções seguidas, ainda é lido; e um uso aberto é uma
    execução que ainda vai copiar o nome para o `job.json`. Os dois esperam.
    """
    chosen = list(
        session.execute(
            select(upload.c.id)
            .where(
                upload.c.original_name.is_not(None),
                upload.c.deleted_at.is_not(None),
                upload.c.created_at <= now - NAME_AFTER,
                ~_open_use_of(upload.c.object_id),
            )
            .order_by(upload.c.created_at, upload.c.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).scalars()
    )
    if chosen:
        session.execute(update(upload).where(upload.c.id.in_(chosen)).values(original_name=None))
    return len(chosen)


@dataclass(frozen=True)
class HistoryReferences:
    """Como `runs` diz se o histórico ainda aponta para um upload ou artefato.

    `files` fica abaixo de `runs` nas camadas e não lê as tabelas dela: quem chama passa os
    predicados (`EXISTS` correlacionado sobre `run_input` e `run_output`).
    """

    upload: Callable[[ColumnElement[uuid.UUID]], ColumnElement[bool]]
    artifact: Callable[[ColumnElement[uuid.UUID]], ColumnElement[bool]]


def delete_old_file_rows(
    session: Session, now: datetime, limit: int, history: HistoryReferences
) -> int:
    """Apaga as linhas de objetos que já saíram do disco e que o histórico não usa mais.

    Ordem segura: a linha só sai depois do arquivo (`deleted` ou `missing`), nunca antes, para
    nenhum arquivo ficar no disco sem registro. Upload e artefato saem aos 90 dias da criação e só
    sem `run_input`/`run_output` apontando para eles, isto é, depois da remoção da Run
    (`runs.retention.delete_old_runs`). Usos encerrados há 90 dias saem no mesmo lote. Devolve
    quantos objetos foram apagados.
    """
    cutoff = now - FILE_ROWS_AFTER
    _delete_old_ended_uses(session, cutoff, limit)
    blocking_upload = exists().where(
        upload.c.object_id == stored_object.c.id,
        or_(
            upload.c.deleted_at.is_(None),
            upload.c.created_at > cutoff,
            history.upload(upload.c.id),
        ),
    )
    blocking_artifact = exists().where(
        artifact.c.object_id == stored_object.c.id,
        or_(
            artifact.c.deleted_at.is_(None),
            artifact.c.created_at > cutoff,
            history.artifact(artifact.c.id),
        ),
    )
    any_use = exists().where(object_use.c.object_id == stored_object.c.id)
    chosen = list(
        session.execute(
            select(stored_object.c.id)
            .where(
                stored_object.c.state.in_(_GONE_FROM_DISK),
                ~any_use,
                ~blocking_upload,
                ~blocking_artifact,
            )
            .order_by(stored_object.c.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).scalars()
    )
    if chosen:
        session.execute(delete(upload).where(upload.c.object_id.in_(chosen)))
        session.execute(delete(artifact).where(artifact.c.object_id.in_(chosen)))
        session.execute(delete(stored_object).where(stored_object.c.id.in_(chosen)))
    return len(chosen)


def file_backlog(session: Session, now: datetime) -> dict[str, int]:
    """O que ainda falta anular ou apagar em `files`: a métrica do tamanho do acúmulo."""
    deleted_before = (upload.c.deleted_at.is_not(None),)
    to_anonymize: int = session.execute(
        select(func.count()).where(
            *deleted_before,
            upload.c.original_name.is_not(None),
            upload.c.created_at <= now - NAME_AFTER,
        )
    ).scalar_one()
    to_delete: int = session.execute(
        select(func.count()).where(*deleted_before, upload.c.created_at <= now - FILE_ROWS_AFTER)
    ).scalar_one()
    return {"uploads_to_anonymize": to_anonymize, "uploads_to_delete": to_delete}


def attempts_with_unsettled_candidates(
    session: Session, attempt_ids: Collection[uuid.UUID]
) -> set[uuid.UUID]:
    """Tentativas com candidato que o descarte ainda precisa achar pela tentativa.

    Pendente (sem conteúdo confirmado) ou confirmado sem upload nem artefato válido. Quem já foi
    pedido para exclusão segue pelo expurgo, que não depende da tentativa.
    """
    if not attempt_ids:
        return set()
    unsettled = or_(
        stored_object.c.state == ObjectState.PENDING,
        (stored_object.c.state == ObjectState.COMMITTED) & ~has_valid_reference(),
    )
    return set(
        session.execute(
            select(stored_object.c.attempt_id)
            .where(stored_object.c.attempt_id.in_(attempt_ids), unsettled)
            .distinct()
        ).scalars()
    )


def next_storage_release_in(session: Session, owner_id: uuid.UUID) -> timedelta | None:
    """Tempo até o próximo upload ou artefato válido da conta vencer; `None` se não houver.

    Base do `Retry-After` da cota de armazenamento: é a próxima liberação que se sabe prever.
    """
    pending = union_all(
        select(upload.c.expires_at.label("expires_at")).where(
            upload.c.owner_id == owner_id, upload.c.deleted_at.is_(None)
        ),
        select(artifact.c.expires_at.label("expires_at")).where(
            artifact.c.owner_id == owner_id, artifact.c.deleted_at.is_(None)
        ),
    ).subquery()
    wait: timedelta | None = session.execute(
        select(func.greatest(func.min(pending.c.expires_at) - func.now(), timedelta(0)))
    ).scalar_one()
    return wait


def reconcile_capacity(session: Session, *, limit: int) -> capacity.ReconcileReport:
    """Reconcilia os pools e adota objetos que ocupam espaço sem reserva (plano da fatia 6, 3.3).

    Durante o deploy da 0006, a API anterior cria uploads e o worker anterior publica artefatos
    sem reserva. Candidatos de uma tentativa ainda sem artefato ficam de fora: a reserva da Run
    os cobre. Numa transação própria e curta.
    """
    with session.begin():
        orphans = session.execute(
            select(stored_object.c.id, stored_object.c.owner_id, stored_object.c.size_bytes)
            .where(
                stored_object.c.state.in_([ObjectState.COMMITTED, ObjectState.DELETE_REQUESTED]),
                or_(stored_object.c.attempt_id.is_(None), _published_as_artifact()),
                ~capacity.has_live_reservation(capacity.HolderKind.OBJECT, stored_object.c.id),
            )
            .order_by(stored_object.c.id)
            .limit(limit)
        ).all()
        return capacity.reconcile(
            session,
            limit=limit,
            unreserved=[
                capacity.Unreserved(o.owner_id, capacity.HolderKind.OBJECT, o.id, o.size_bytes)
                for o in orphans
            ],
        )


def _open_use_of(object_id: ColumnElement[uuid.UUID]) -> ColumnElement[bool]:
    return exists().where(object_use.c.object_id == object_id, object_use.c.ended_at.is_(None))


def _delete_old_ended_uses(session: Session, cutoff: datetime, limit: int) -> None:
    chosen = (
        select(object_use.c.id)
        .where(object_use.c.ended_at <= cutoff)
        .limit(limit)
        .with_for_update(skip_locked=True)
        .scalar_subquery()
    )
    session.execute(delete(object_use).where(object_use.c.id.in_(chosen)))


def _published_as_artifact() -> ColumnElement[bool]:
    return exists().where(artifact.c.object_id == stored_object.c.id)


def _within_governor(
    candidates: Sequence[_Candidate],
) -> list[_Candidate]:
    """Prefixo dos candidatos que cabe no teto de objetos e de bytes do ciclo.

    O primeiro candidato sempre entra: um objeto maior que o teto de bytes, sozinho na frente
    da fila, travaria a remoção para sempre, e apagar um único objeto não é a remoção em massa
    que o limitador existe para conter.
    """
    chosen: list[_Candidate] = []
    total_bytes = 0
    for candidate in candidates:
        over_bytes = bool(chosen) and total_bytes + candidate.size_bytes > PURGE_MAX_BYTES
        if len(chosen) == PURGE_MAX_OBJECTS or over_bytes:
            break
        chosen.append(candidate)
        total_bytes += candidate.size_bytes
    return chosen
