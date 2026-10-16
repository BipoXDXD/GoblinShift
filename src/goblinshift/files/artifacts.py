"""Artefatos: candidatos, publicação e abertura para download (docs/GoblinShiftPlan.md, 3.4 e 3.6).

Superfície mínima para o worker e as rotas da fatia seguinte. As funções rodam na transação de
quem chama; a transferência de bytes acontece fora dela, entre `open_for_download` e
`finish_download`.
"""

import uuid
from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, insert, select, update
from sqlalchemy.orm import Session

from goblinshift.files.errors import (
    ArtifactGoneError,
    ArtifactNotFoundError,
    ObjectNotAvailableError,
)
from goblinshift.files.limits import ARTIFACT_TTL
from goblinshift.files.objects import hold, release, request_deletion_of_unreferenced
from goblinshift.files.states import ArtifactRole, Availability, HolderKind, ObjectState
from goblinshift.files.tables import artifact, stored_object


@dataclass(frozen=True)
class CandidateObject:
    object_id: uuid.UUID
    storage_key: str


@dataclass(frozen=True)
class ArtifactView:
    id: uuid.UUID
    object_id: uuid.UUID
    role: ArtifactRole
    size_bytes: int
    sha256: bytes
    created_at: datetime
    expires_at: datetime
    availability: Availability


@dataclass(frozen=True)
class DownloadTicket:
    """Autorização de um download em andamento; o uso fica aberto até `finish_download`."""

    holder_id: uuid.UUID
    artifact_id: uuid.UUID
    storage_key: str
    size_bytes: int
    sha256: bytes
    role: ArtifactRole
    file_name: str


def create_candidate_object(
    session: Session,
    owner_id: uuid.UUID,
    run_id: uuid.UUID,
    attempt_id: uuid.UUID,
    generation: int,
) -> CandidateObject:
    """Reserva um objeto pendente para uma saída da tentativa.

    A chave `runs/{run_id}/g{generation}/…` diz ao coletor exatamente o que pertence a uma
    geração vencida (3.4); o sufixo é aleatório e não vem do nome lógico da saída.
    """
    storage_key = f"runs/{run_id}/g{generation}/{uuid.uuid4().hex}"
    object_id: uuid.UUID = session.execute(
        insert(stored_object)
        .values(
            owner_id=owner_id,
            storage_key=storage_key,
            attempt_id=attempt_id,
            generation=generation,
        )
        .returning(stored_object.c.id)
    ).scalar_one()
    return CandidateObject(object_id, storage_key)


def commit_candidate(
    session: Session, owner_id: uuid.UUID, object_id: uuid.UUID, sha256: bytes, size_bytes: int
) -> None:
    """Registra o conteúdo persistido de um candidato; só vale para candidato ainda pendente."""
    committed = session.execute(
        update(stored_object)
        .where(
            stored_object.c.id == object_id,
            stored_object.c.owner_id == owner_id,
            stored_object.c.state == ObjectState.PENDING,
            stored_object.c.attempt_id.is_not(None),
        )
        .values(
            state=ObjectState.COMMITTED,
            sha256=sha256,
            size_bytes=size_bytes,
            committed_at=func.now(),
        )
        .returning(stored_object.c.id)
    ).scalar_one_or_none()
    if committed is None:
        raise ObjectNotAvailableError


def publish_artifact(
    session: Session,
    owner_id: uuid.UUID,
    object_id: uuid.UUID,
    role: ArtifactRole,
    file_name: str,
) -> ArtifactView:
    """Cria o artefato sobre um objeto `committed` do dono, com retenção de 48 h (1.7).

    Aceita também o objeto de uma entrada (reaproveitamento do Detox). O `FOR SHARE` impede que
    o expurgo remova o objeto enquanto a referência nova nasce.
    """
    content = session.execute(
        select(stored_object.c.size_bytes, stored_object.c.sha256)
        .where(
            stored_object.c.id == object_id,
            stored_object.c.owner_id == owner_id,
            stored_object.c.state == ObjectState.COMMITTED,
        )
        .with_for_update(read=True)
    ).one_or_none()
    if content is None:
        raise ObjectNotAvailableError
    row = session.execute(
        insert(artifact)
        .values(
            owner_id=owner_id,
            object_id=object_id,
            role=role,
            file_name=file_name,
            expires_at=func.now() + ARTIFACT_TTL,
        )
        .returning(artifact.c.id, artifact.c.created_at, artifact.c.expires_at)
    ).one()
    return ArtifactView(
        id=row.id,
        object_id=object_id,
        role=role,
        size_bytes=content.size_bytes,
        sha256=content.sha256,
        created_at=row.created_at,
        expires_at=row.expires_at,
        availability=Availability.AVAILABLE,
    )


def get_artifact(session: Session, owner_id: uuid.UUID, artifact_id: uuid.UUID) -> ArtifactView:
    """Metadados do artefato do dono, inclusive expirado ou excluído (com a disponibilidade).

    Só o download recusa conteúdo indisponível; os metadados continuam consultáveis até a
    retenção remover a linha.
    """
    row = session.execute(
        select(
            artifact.c.id,
            artifact.c.object_id,
            artifact.c.role,
            artifact.c.created_at,
            artifact.c.expires_at,
            artifact.c.deleted_at,
            (artifact.c.expires_at <= func.now()).label("expired"),
            stored_object.c.size_bytes,
            stored_object.c.sha256,
        )
        .join(stored_object, stored_object.c.id == artifact.c.object_id)
        .where(artifact.c.id == artifact_id, artifact.c.owner_id == owner_id)
    ).one_or_none()
    if row is None:
        raise ArtifactNotFoundError
    return ArtifactView(
        id=row.id,
        object_id=row.object_id,
        role=ArtifactRole(row.role),
        size_bytes=row.size_bytes,
        sha256=row.sha256,
        created_at=row.created_at,
        expires_at=row.expires_at,
        availability=Availability.of(
            deleted_at=row.deleted_at, expires_at=row.expires_at, expired=row.expired
        ),
    )


def open_for_download(
    session: Session, owner_id: uuid.UUID, artifact_id: uuid.UUID
) -> DownloadTicket:
    """Revalida o acesso e registra o uso de download (3.6).

    A exclusão posterior bloqueia novos downloads, mas a remoção física espera este terminar.
    """
    row = session.execute(
        select(
            artifact.c.object_id,
            artifact.c.role,
            artifact.c.file_name,
            artifact.c.deleted_at,
            artifact.c.expires_at <= func.now(),
            stored_object.c.storage_key,
            stored_object.c.size_bytes,
            stored_object.c.sha256,
        )
        .join(stored_object, stored_object.c.id == artifact.c.object_id)
        .where(artifact.c.id == artifact_id, artifact.c.owner_id == owner_id)
        .with_for_update(read=True, of=artifact)
    ).one_or_none()
    if row is None:
        raise ArtifactNotFoundError
    object_id, role, file_name, deleted_at, expired, storage_key, size_bytes, sha256 = row
    if deleted_at is not None or expired:
        raise ArtifactGoneError
    holder_id = uuid.uuid7()
    try:
        hold(session, [object_id], HolderKind.DOWNLOAD, holder_id)
    except ObjectNotAvailableError:
        # Artefato válido sobre objeto ausente (`missing`): para o dono, o conteúdo sumiu.
        raise ArtifactGoneError from None
    return DownloadTicket(
        holder_id, artifact_id, storage_key, size_bytes, sha256, ArtifactRole(role), file_name
    )


def finish_download(session: Session, ticket: DownloadTicket) -> None:
    """Encerra o uso do download; repetir é no-op."""
    release(session, HolderKind.DOWNLOAD, ticket.holder_id)


def request_artifacts_deletion(
    session: Session, owner_id: uuid.UUID, artifact_ids: Collection[uuid.UUID]
) -> None:
    """Torna os artefatos do dono indisponíveis na hora; os já excluídos ficam como estão.

    Ordem de locks de 3.4: Artifact (`FOR UPDATE`, em ordem de ID) → StoredObject. O objeto só
    vira `delete_requested` se nenhum upload ou outro artefato válido o aponta: a saída do Detox
    que reaproveita o objeto da entrada (ADR 020) não apaga o original.
    """
    rows = session.execute(
        select(artifact.c.id, artifact.c.object_id)
        .where(
            artifact.c.owner_id == owner_id,
            artifact.c.id.in_(sorted(set(artifact_ids))),
            artifact.c.deleted_at.is_(None),
        )
        .order_by(artifact.c.id)
        .with_for_update()
    ).all()
    if not rows:
        return
    session.execute(
        update(artifact)
        .where(artifact.c.id.in_([row.id for row in rows]))
        .values(deleted_at=func.now())
    )
    request_deletion_of_unreferenced(session, [row.object_id for row in rows])
