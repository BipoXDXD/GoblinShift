"""Lote de upload criado uma vez por Idempotency-Key (#442, item 57a; 1.6).

O mesmo desenho da aceitação de execuções (`runs.acceptance`), sem o registro de idempotência,
que tem FK obrigatória para `run` e fica num módulo que `files` não importa: a chave, o hash do
corpo e a posição de cada item ficam nas linhas do próprio lote. Na transação de quem chama:

1. Trava da chave (`pg_try_advisory_xact_lock`, sem espera): outro pedido com a mesma chave em
   andamento é recusado na hora, em vez de esperar ou criar um segundo lote.
2. Lote vigente da chave, criado há menos de `INCOMPLETE_UPLOAD_TTL` (o prazo de uma intenção):
   mesmo hash → replay com o estado atual de cada upload, sem reservar de novo; hash diferente →
   recusa.
3. Sem lote vigente, `create_intents` grava o lote com a chave.
"""

import hashlib
import json
from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from goblinshift.files.errors import UploadRequestInProgressError, UploadRequestKeyReusedError
from goblinshift.files.limits import INCOMPLETE_UPLOAD_TTL
from goblinshift.files.tables import upload
from goblinshift.files.uploads import IdempotentRequest, UploadView, create_intents, get_uploads

CREATE_UPLOADS_OPERATION = "create_uploads"
# Classe do advisory lock, ao lado das de `runs.acceptance` (0x6773_0001 e 0x6773_0002).
ADVISORY_CLASS_UPLOAD_REQUEST = 0x6773_0003
_CANONICAL_SEPARATORS = (",", ":")


def create_intents_once(
    session: Session, owner_id: UUID, items: Sequence[tuple[str, int]], key: str
) -> tuple[list[UploadView], bool]:
    """Cria o lote ou, se a chave já criou um com o mesmo corpo, devolve-o; o `bool` é replay.

    Levanta `UploadRequestInProgressError` se outro pedido com a chave está em curso e
    `UploadRequestKeyReusedError` se a chave vigente veio com outro corpo. O formato da chave é
    de quem chama; o banco o confere de novo (CHECK).
    """
    _lock_request_key(session, owner_id, key)
    request = IdempotentRequest(key, _body_hash(items))
    existing = _live_batch(session, owner_id, request)
    if existing is not None:
        return existing, True
    return create_intents(session, owner_id, items, request), False


def _lock_request_key(session: Session, owner_id: UUID, key: str) -> None:
    """Sem espera: se outra transação tem a chave, o pedido original ainda está em curso.

    O objeto da trava é um hash de 32 bits; uma colisão entre chaves em voo ao mesmo tempo só
    produz um 409 a mais, que o cliente repete após o Retry-After.
    """
    acquired = session.execute(
        select(
            func.pg_try_advisory_xact_lock(
                ADVISORY_CLASS_UPLOAD_REQUEST,
                func.hashtext(f"{owner_id}:{CREATE_UPLOADS_OPERATION}:{key}"),
            )
        )
    ).scalar_one()
    if not acquired:
        raise UploadRequestInProgressError


def _body_hash(items: Sequence[tuple[str, int]]) -> bytes:
    canonical = json.dumps(
        [[name, size] for name, size in items], ensure_ascii=False, separators=_CANONICAL_SEPARATORS
    )
    return hashlib.sha256(canonical.encode()).digest()


def _live_batch(
    session: Session, owner_id: UUID, request: IdempotentRequest
) -> list[UploadView] | None:
    """O lote vigente da chave, com o estado atual de cada upload, ou `None` se não há.

    As linhas de um lote nascem na mesma transação, com o mesmo `created_at`: vencem juntas. Um
    upload incompleto que o cliente excluiu e a retenção já removeu fica fora da resposta.
    """
    rows = session.execute(
        select(upload.c.id, upload.c.request_hash)
        .where(
            upload.c.owner_id == owner_id,
            upload.c.request_key == request.key,
            upload.c.created_at > func.now() - INCOMPLETE_UPLOAD_TTL,
        )
        .order_by(upload.c.request_index)
    ).all()
    if not rows:
        return None
    if bytes(rows[0].request_hash) != request.body_hash:
        raise UploadRequestKeyReusedError
    return get_uploads(session, owner_id, [row.id for row in rows])
