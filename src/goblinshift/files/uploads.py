"""Uploads: intenções, recebimento de conteúdo, consulta e exclusão (docs/GoblinShiftPlan.md, 3.6).

Transaction scripts sobre a Session (3.2). `receive_content` abre as próprias transações, porque a
transferência acontece entre duas delas; as demais funções rodam na transação de quem chama.
Prazos usam o relógio do banco (`now()`), o mesmo para API, worker e manutenção.
"""

import logging
import re
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, NamedTuple, NoReturn

from sqlalchemy import ColumnElement, Row, func, insert, select, update
from sqlalchemy.orm import Session

from goblinshift import capacity
from goblinshift.files.errors import (
    InputNotAvailableError,
    InvalidUploadRequestError,
    UploadContentTooLargeError,
    UploadGoneError,
    UploadNotFoundError,
    UploadSizeMismatchError,
    UploadStateConflictError,
)
from goblinshift.files.limits import (
    INCOMPLETE_UPLOAD_TTL,
    MAX_BATCH_BYTES,
    MAX_BATCH_FILES,
    MAX_FILE_BYTES,
    MAX_NAME_CHARS,
    READY_UPLOAD_TTL,
)
from goblinshift.files.objects import request_deletion_of_unreferenced
from goblinshift.files.states import Availability, ObjectState, UploadState
from goblinshift.files.storage import ContentTooLargeError, FileStorage
from goblinshift.files.tables import stored_object, upload

logger = logging.getLogger(__name__)

_UPLOAD_KEY_PREFIX = "uploads"
_NAME_SEPARATORS = frozenset("/\\")
# Controles (Cc) e separadores de linha/parágrafo (Zl, Zp), em faixas escritas à mão porque o JSON
# Schema (ECMA-262) não tem `\p{...}`; um teste confere que elas cobrem exatamente essas categorias.
_CONTROL_CHARS = r"\x00-\x1f\x7f-\x9f\u2028\u2029"
# Mais os surrogates soltos (Cs), que o PostgreSQL nem aceitaria em UTF-8.
_FORBIDDEN_NAME_CHAR = re.compile(rf"[{_CONTROL_CHARS}\ud800-\udfff]")
# Publicado no contrato (`pattern` de `items[].name`); vale em Python e em ECMA-262. Fora dele
# ficam o comprimento, com minLength/maxLength próprios; os nomes "." e "..", que exigiriam
# lookahead; e os surrogates soltos, cuja faixa num conjunto negado o gerador do Schemathesis
# 4.31 interpreta errado (gera caracteres que o padrão recusa). Todos seguem recusados aqui.
UPLOAD_NAME_PATTERN = rf"^[^/\\{_CONTROL_CHARS}]*$"


class UploadIntent(NamedTuple):
    name: str
    expected_size: int


@dataclass(frozen=True)
class UploadView:
    id: uuid.UUID
    # `None` 30 dias depois, só em upload excluído (1.7, "Retenção").
    original_name: str | None
    expected_size: int
    state: UploadState
    # Conhecidos só depois que o conteúdo foi persistido.
    size_bytes: int | None
    sha256: bytes | None
    created_at: datetime
    ready_at: datetime | None
    expires_at: datetime
    deleted_at: datetime | None
    availability: Availability


@dataclass(frozen=True)
class ReadyUpload:
    upload_id: uuid.UUID
    object_id: uuid.UUID
    original_name: str
    size_bytes: int
    sha256: bytes


def create_intents(
    session: Session, owner_id: uuid.UUID, items: Sequence[tuple[str, int]]
) -> list[UploadView]:
    """Cria um lote de intenções de upload, na ordem recebida, cada uma com seu objeto pendente.

    Reserva o tamanho declarado de cada intenção (detentor `upload`), tudo ou nada: acima do teto
    levanta `AccountCapacityExceededError` ou `GlobalCapacityExceededError`, e quem chama desfaz.
    """
    intents = _parse_intents(items)
    object_ids = session.execute(
        insert(stored_object).returning(stored_object.c.id, sort_by_parameter_order=True),
        [{"owner_id": owner_id, "storage_key": _new_upload_key()} for _ in intents],
    ).scalars()
    rows = session.execute(
        insert(upload)
        .values(expires_at=func.now() + INCOMPLETE_UPLOAD_TTL)
        .returning(*upload.c, sort_by_parameter_order=True),
        [
            {
                "owner_id": owner_id,
                "object_id": object_id,
                "original_name": intent.name,
                "expected_size": intent.expected_size,
            }
            for intent, object_id in zip(intents, object_ids, strict=True)
        ],
    ).all()
    capacity.reserve_many(
        session,
        owner_id=owner_id,
        holder=capacity.HolderKind.UPLOAD,
        resource=capacity.Resource.STORAGE_BYTES,
        amounts={row.id: row.expected_size for row in rows},
    )
    return [
        _view(row, size_bytes=None, sha256=None, availability=Availability.AVAILABLE)
        for row in rows
    ]


def receive_content(
    session: Session,
    storage: FileStorage,
    owner_id: uuid.UUID,
    upload_id: uuid.UUID,
    chunks: Iterable[bytes],
) -> UploadView:
    """Grava o conteúdo de um upload `created` e o deixa `ready`.

    Uma transação curta reserva o único escritor (`created → receiving`), a transferência para o
    storage acontece sem transação aberta, e outra transação curta conclui. Qualquer falha no
    caminho deixa o upload `failed`, sem reabri-lo, e nenhum arquivo sob a chave.
    """
    with session.begin():
        storage_key, expected_size = _claim_writer(session, owner_id, upload_id)
    try:
        sha256, size = storage.create_exclusive(storage_key, chunks, max_bytes=expected_size)
    except ContentTooLargeError:
        _fail(session, storage, owner_id, upload_id, storage_key)
        raise UploadContentTooLargeError(expected_size) from None
    except Exception:
        _fail(session, storage, owner_id, upload_id, storage_key)
        raise
    if size != expected_size:
        _fail(session, storage, owner_id, upload_id, storage_key)
        raise UploadSizeMismatchError(expected_size)
    with session.begin():
        completed = _complete(session, owner_id, upload_id, sha256, size)
    if not completed:
        # Excluído ou expirado durante a transferência: o arquivo novo não tem dono.
        storage.delete(storage_key)
        with session.begin():
            _raise_unavailable(session, owner_id, upload_id)
    with session.begin():
        return get_upload(session, owner_id, upload_id)


def get_upload(session: Session, owner_id: uuid.UUID, upload_id: uuid.UUID) -> UploadView:
    row = session.execute(
        select(
            *upload.c,
            stored_object.c.size_bytes,
            stored_object.c.sha256,
            (upload.c.expires_at <= func.now()).label("expired"),
        )
        .join(stored_object, stored_object.c.id == upload.c.object_id)
        .where(upload.c.id == upload_id, upload.c.owner_id == owner_id)
    ).one_or_none()
    if row is None:
        raise UploadNotFoundError
    return _view(
        row,
        size_bytes=row.size_bytes,
        sha256=row.sha256,
        availability=Availability.of(
            deleted_at=row.deleted_at, expires_at=row.expires_at, expired=row.expired
        ),
    )


def request_deletion(session: Session, owner_id: uuid.UUID, upload_id: uuid.UUID) -> None:
    """Torna o upload indisponível na hora; repetir é sucesso.

    Ordem de locks de 3.4: Upload (`FOR UPDATE`) → StoredObject. O arquivo só sai do disco pela
    manutenção, quando não houver uso aberto. Upload incompleto não tem conteúdo confirmado: a
    retenção remove objeto e linha.
    """
    row = session.execute(
        select(upload.c.object_id, upload.c.deleted_at)
        .where(upload.c.id == upload_id, upload.c.owner_id == owner_id)
        .with_for_update()
    ).one_or_none()
    if row is None:
        raise UploadNotFoundError
    if row.deleted_at is not None:
        return
    session.execute(update(upload).where(upload.c.id == upload_id).values(deleted_at=func.now()))
    request_deletion_of_unreferenced(session, [row.object_id])


def lock_ready_uploads(
    session: Session, owner_id: uuid.UUID, upload_ids: Sequence[uuid.UUID]
) -> list[ReadyUpload]:
    """Confere e trava (`FOR SHARE`, em ordem de ID) uploads do dono prontos para uma execução.

    Devolve na ordem pedida. Qualquer um indisponível, pelo motivo que for, gera um único
    `InputNotAvailableError`. O `FOR SHARE` impede que a exclusão (`FOR UPDATE`) passe na frente
    até o fim da transação de quem chama, que registra o uso com `hold`.
    """
    if len(set(upload_ids)) != len(upload_ids):
        # Contrato de quem chama: `runs` recusa IDs repetidos antes de chegar aqui (1.6).
        raise ValueError("upload_ids repetidos")
    rows = session.execute(
        select(
            upload.c.id,
            upload.c.object_id,
            upload.c.original_name,
            stored_object.c.size_bytes,
            stored_object.c.sha256,
        )
        .join(stored_object, stored_object.c.id == upload.c.object_id)
        .where(
            upload.c.id.in_(upload_ids),
            upload.c.owner_id == owner_id,
            upload.c.state == UploadState.READY,
            upload.c.deleted_at.is_(None),
            upload.c.expires_at > func.now(),
            stored_object.c.state == ObjectState.COMMITTED,
        )
        .order_by(upload.c.id)
        .with_for_update(read=True, of=upload)
    ).all()
    if len(rows) != len(upload_ids):
        raise InputNotAvailableError
    by_id = {
        row.id: ReadyUpload(row.id, row.object_id, row.original_name, row.size_bytes, row.sha256)
        for row in rows
    }
    return [by_id[upload_id] for upload_id in upload_ids]


def stored_size_of(upload_id: ColumnElement[uuid.UUID]) -> ColumnElement[int]:
    """Bytes guardados do upload (0 se ainda sem conteúdo), como subconsulta correlacionada.

    Para `runs` somar a entrada processada sem ler as tabelas de `files`.
    """
    return (
        select(func.coalesce(stored_object.c.size_bytes, 0))
        .join(upload, upload.c.object_id == stored_object.c.id)
        .where(upload.c.id == upload_id)
        .scalar_subquery()
    )


def _parse_intents(items: Sequence[tuple[str, int]]) -> list[UploadIntent]:
    if not 1 <= len(items) <= MAX_BATCH_FILES:
        raise InvalidUploadRequestError("items", f"o lote deve ter de 1 a {MAX_BATCH_FILES} itens")
    intents = [UploadIntent(*item) for item in items]
    for index, intent in enumerate(intents):
        name_problem = _name_problem(intent.name)
        if name_problem is not None:
            raise InvalidUploadRequestError(f"items[{index}].name", name_problem)
        if not 0 <= intent.expected_size <= MAX_FILE_BYTES:
            raise InvalidUploadRequestError(
                f"items[{index}].expected_size", f"deve estar entre 0 e {MAX_FILE_BYTES} bytes"
            )
    if sum(intent.expected_size for intent in intents) > MAX_BATCH_BYTES:
        raise InvalidUploadRequestError("items", f"o lote passa de {MAX_BATCH_BYTES} bytes")
    return intents


def _name_problem(name: str) -> str | None:
    """Nomes são dados, nunca caminhos (3.6): recusa o que poderia virar caminho ou controle."""
    if not 1 <= len(name) <= MAX_NAME_CHARS:
        return f"deve ter de 1 a {MAX_NAME_CHARS} caracteres"
    if name in {".", ".."}:
        return "não pode ser '.' nem '..'"
    if any(char in _NAME_SEPARATORS for char in name):
        return "não pode conter separadores de caminho"
    if _FORBIDDEN_NAME_CHAR.search(name):
        return "não pode conter caracteres de controle"
    return None


def _new_upload_key() -> str:
    # Aleatória e sem relação com o nome do usuário.
    return f"{_UPLOAD_KEY_PREFIX}/{uuid.uuid4().hex}"


def _claim_writer(session: Session, owner_id: uuid.UUID, upload_id: uuid.UUID) -> tuple[str, int]:
    """`created → receiving` por atualização condicional: só um escritor passa."""
    row = session.execute(
        update(upload)
        .where(
            upload.c.id == upload_id,
            upload.c.owner_id == owner_id,
            upload.c.state == UploadState.CREATED,
            upload.c.deleted_at.is_(None),
            upload.c.expires_at > func.now(),
            stored_object.c.id == upload.c.object_id,
        )
        .values(state=UploadState.RECEIVING, expires_at=func.now() + INCOMPLETE_UPLOAD_TTL)
        .returning(stored_object.c.storage_key, upload.c.expected_size)
    ).one_or_none()
    if row is None:
        _raise_unavailable(session, owner_id, upload_id)
    return row.storage_key, row.expected_size


def _complete(
    session: Session, owner_id: uuid.UUID, upload_id: uuid.UUID, sha256: bytes, size: int
) -> bool:
    """`receiving → ready` e objeto `committed`, na mesma transação; `False` se perdeu a vez.

    A reserva da intenção passa para o objeto pelo tamanho real, que é o declarado.
    """
    object_id = session.execute(
        update(upload)
        .where(
            upload.c.id == upload_id,
            upload.c.owner_id == owner_id,
            upload.c.state == UploadState.RECEIVING,
            upload.c.deleted_at.is_(None),
        )
        .values(
            state=UploadState.READY,
            ready_at=func.now(),
            expires_at=func.now() + READY_UPLOAD_TTL,
        )
        .returning(upload.c.object_id)
    ).scalar_one_or_none()
    if object_id is None:
        return False
    session.execute(
        update(stored_object)
        .where(stored_object.c.id == object_id, stored_object.c.state == ObjectState.PENDING)
        .values(
            state=ObjectState.COMMITTED, sha256=sha256, size_bytes=size, committed_at=func.now()
        )
    )
    _move_reservation_to_object(session, upload_id, object_id, size)
    return True


def _move_reservation_to_object(
    session: Session, upload_id: uuid.UUID, object_id: uuid.UUID, size: int
) -> None:
    try:
        capacity.transfer(
            session,
            from_holder=(capacity.HolderKind.UPLOAD, upload_id),
            to_holder=(capacity.HolderKind.OBJECT, object_id),
            amount=size,
        )
    except capacity.ReservationNotFoundError:
        # Intenção criada pela API anterior ao deploy da 0006, que não reservava. O upload
        # segue; a reconciliação da manutenção adota o objeto no ciclo seguinte.
        logger.warning(
            "upload sem reserva de capacidade",
            extra={"fields": {"alert": "upload_without_reservation", "upload_id": str(upload_id)}},
        )


def _fail(
    session: Session,
    storage: FileStorage,
    owner_id: uuid.UUID,
    upload_id: uuid.UUID,
    storage_key: str,
) -> None:
    """`receiving → failed`, libera a reserva e remove o que tiver chegado ao disco.

    O objeto segue `pending` até a retenção remover linha e objeto.
    """
    with session.begin():
        failed = session.execute(
            update(upload)
            .where(
                upload.c.id == upload_id,
                upload.c.owner_id == owner_id,
                upload.c.state == UploadState.RECEIVING,
            )
            .values(state=UploadState.FAILED)
            .returning(upload.c.id)
        ).scalar_one_or_none()
        if failed is not None:
            capacity.release(session, holder=capacity.HolderKind.UPLOAD, holder_id=upload_id)
    storage.delete(storage_key)


def _raise_unavailable(session: Session, owner_id: uuid.UUID, upload_id: uuid.UUID) -> NoReturn:
    """Explica por que o upload não aceitou a operação, sem revelar uploads de outra conta."""
    row = session.execute(
        select(upload.c.state, upload.c.deleted_at, upload.c.expires_at <= func.now()).where(
            upload.c.id == upload_id, upload.c.owner_id == owner_id
        )
    ).one_or_none()
    if row is None:
        raise UploadNotFoundError
    state, deleted_at, expired = row
    if deleted_at is not None or expired:
        raise UploadGoneError
    raise UploadStateConflictError(UploadState(state))


def _view(
    row: Row[Any, *tuple[Any, ...]],
    *,
    size_bytes: int | None,
    sha256: bytes | None,
    availability: Availability,
) -> UploadView:
    return UploadView(
        id=row.id,
        original_name=row.original_name,
        expected_size=row.expected_size,
        state=UploadState(row.state),
        size_bytes=size_bytes,
        sha256=sha256,
        created_at=row.created_at,
        ready_at=row.ready_at,
        expires_at=row.expires_at,
        deleted_at=row.deleted_at,
        availability=availability,
    )
