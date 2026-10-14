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
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import Row, delete, exists, func, or_, select, update
from sqlalchemy.orm import Session

from goblinshift.files.limits import EXPIRY_BATCH_SIZE, PURGE_MAX_BYTES, PURGE_MAX_OBJECTS
from goblinshift.files.objects import request_deletion_of_unreferenced, select_purgeable
from goblinshift.files.states import ObjectState, UploadState
from goblinshift.files.storage import FileStorage
from goblinshift.files.tables import artifact, object_use, stored_object, upload

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
    apaga o próprio arquivo.
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
            session.execute(delete(upload).where(upload.c.id.in_([r.id for r in rows])))
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
    Busca um candidato a mais para saber se o limite cortou o lote.
    """
    with session.begin():
        candidates = session.execute(select_purgeable(limit=PURGE_MAX_OBJECTS + 1)).all()
        chosen = _within_governor(candidates)
        if chosen:
            session.execute(
                update(stored_object)
                .where(stored_object.c.id.in_([c.id for c in chosen]))
                .values(state=ObjectState.DELETED)
            )
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
