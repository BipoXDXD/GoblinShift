"""Candidatos de saída: descarte e coleta de lixo (docs/GoblinShiftPlan.md, 3.4).

Um candidato é um `StoredObject` com `attempt_id`: nasceu de uma tentativa e só vira conteúdo
visível quando a publicação cria um `Artifact` sobre ele. `files` não sabe se a tentativa venceu;
quem sabe é `runs`, que decide o descarte e chama estas funções na própria transação.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from goblinshift.files.objects import has_valid_reference, request_deletion_of_unreferenced
from goblinshift.files.states import ObjectState
from goblinshift.files.tables import stored_object, upload


@dataclass(frozen=True)
class HeldContent:
    """Conteúdo de uma entrada já reservada (`hold`) por uma execução."""

    upload_id: uuid.UUID
    original_name: str
    storage_key: str
    size_bytes: int
    sha256: bytes
    object_id: uuid.UUID
    # `False` depois que o dono excluiu o upload (`delete_requested`): a execução ainda lê o
    # conteúdo, mas o objeto não pode ganhar referência nova, como um artefato (ADR 020).
    committed: bool


class HeldContentMissingError(Exception):
    """Uma entrada da execução não tem mais objeto legível: defeito de infraestrutura."""


def discard_candidates(session: Session, attempt_id: uuid.UUID) -> list[str]:
    """Marca para remoção os candidatos da tentativa que não viraram artefato.

    Confirmados sem referência viram `delete_requested`, e o expurgo da manutenção os remove
    com o limitador. Pendentes nunca tiveram conteúdo confirmado: a linha sai aqui, e as chaves
    voltam para quem chama apagar do disco depois do commit (a mesma ordem de `retention`).
    """
    rows = session.execute(
        select(stored_object.c.id, stored_object.c.state, stored_object.c.storage_key)
        .where(
            stored_object.c.attempt_id == attempt_id,
            stored_object.c.state.in_([ObjectState.PENDING, ObjectState.COMMITTED]),
        )
        .order_by(stored_object.c.id)
        .with_for_update()
    ).all()
    pending = [row for row in rows if row.state == ObjectState.PENDING]
    if pending:
        session.execute(
            delete(stored_object).where(stored_object.c.id.in_([row.id for row in pending]))
        )
    request_deletion_of_unreferenced(
        session, [row.id for row in rows if row.state == ObjectState.COMMITTED]
    )
    return [row.storage_key for row in pending]


def attempts_with_loose_candidates(session: Session, limit: int) -> list[uuid.UUID]:
    """Tentativas com candidatos ainda não descartados nem publicados.

    Inclui as vivas: cabe a `runs` filtrar as que já terminaram antes de descartar.
    """
    loose = or_(
        stored_object.c.state == ObjectState.PENDING,
        (stored_object.c.state == ObjectState.COMMITTED) & ~has_valid_reference(),
    )
    return list(
        session.execute(
            select(stored_object.c.attempt_id)
            .where(stored_object.c.attempt_id.is_not(None), loose)
            .group_by(stored_object.c.attempt_id)
            .order_by(stored_object.c.attempt_id)
            .limit(limit)
        ).scalars()
    )


def held_input_contents(
    session: Session, owner_id: uuid.UUID, upload_ids: Sequence[uuid.UUID]
) -> list[HeldContent]:
    """Chave, tamanho e hash das entradas de uma execução, na ordem pedida.

    Não exige upload válido: a pessoa pode excluir o upload depois da aceitação, e o uso aberto
    pela execução mantém o arquivo no disco até o `release` (3.6). Por isso `delete_requested`
    também serve.
    """
    rows = session.execute(
        select(
            upload.c.id,
            upload.c.original_name,
            stored_object.c.storage_key,
            stored_object.c.size_bytes,
            stored_object.c.sha256,
            stored_object.c.id.label("object_id"),
            stored_object.c.state,
        )
        .join(stored_object, stored_object.c.id == upload.c.object_id)
        .where(
            upload.c.id.in_(upload_ids),
            upload.c.owner_id == owner_id,
            stored_object.c.state.in_([ObjectState.COMMITTED, ObjectState.DELETE_REQUESTED]),
        )
    ).all()
    by_id = {
        row.id: HeldContent(
            row.id,
            row.original_name,
            row.storage_key,
            row.size_bytes,
            row.sha256,
            row.object_id,
            committed=row.state == ObjectState.COMMITTED,
        )
        for row in rows
    }
    missing = [upload_id for upload_id in upload_ids if upload_id not in by_id]
    if missing:
        raise HeldContentMissingError(f"{len(missing)} entrada(s) sem objeto legível")
    return [by_id[upload_id] for upload_id in upload_ids]
