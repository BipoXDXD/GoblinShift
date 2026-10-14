"""Aceitação de execuções: `POST /runs` sem HTTP (docs/GoblinShiftPlan.md, 1.6, 1.7 e 3.4).

Fora de transação: formato da Idempotency-Key, parâmetros resolvidos pelo catálogo, forma das
entradas e o hash canônico do comando. Numa transação curta, na ordem de locks de 3.4:

1. Trava da chave de idempotência (`pg_try_advisory_xact_lock`, sem espera). Todo escritor de uma
   chave passa por ela, então outra requisição com a mesma chave em andamento é detectada na
   hora (409 `IDEMPOTENCY_IN_PROGRESS`), em vez de esperar ou criar uma segunda Run. Por não
   esperar, essa trava fica fora da ordem de 3.4 sem risco de deadlock.
2. Registro de idempotência vigente: mesmo hash → replay; hash diferente → recusa. Nada é escrito.
3. Capacidade global: `pg_advisory_xact_lock` numa chave constante serializa as aceitações antes
   de contar a fila, para que duas réplicas não vejam 9 aguardando e entrem as duas (contar e
   inserir sem trava seria write skew). A espera obedece ao `lock_timeout` da conexão.
4. Capacidade da conta: o `INSERT` da Run com `ON CONFLICT DO NOTHING` sobre o índice único
   parcial `run_one_active_per_owner_idx`; sem linha devolvida, já há uma execução ativa.
5. Uploads (`FOR SHARE`), `run_input` na ordem, usos dos objetos e o registro de idempotência
   (`ON CONFLICT … DO UPDATE … WHERE expires_at <= now()`, reuso só depois de vencido).
6. `NOTIFY goblinshift_runs` no mesmo commit: gravar a Run já é enfileirar.

O commit acontece antes de a função devolver; a rota responde 202 só depois dele.
"""

import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import NoReturn
from uuid import UUID

import psycopg.errors
from sqlalchemy import func, insert, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from goblinshift.catalog import (
    CATALOG,
    FieldError,
    InvalidParametersError,
    RecipeVersion,
    RecipeVersionRetiredError,
    UnknownRecipeVersionError,
    VersionStatus,
)
from goblinshift.files import objects
from goblinshift.files.states import HolderKind
from goblinshift.files.uploads import lock_ready_uploads
from goblinshift.runs.lifecycle import QUEUE_WINDOW, Status
from goblinshift.runs.tables import idempotency_record, run, run_input

# Operação gravada no registro de idempotência; a chave é por conta e por operação (1.6).
CREATE_RUN_OPERATION = "create_run"
IDEMPOTENCY_TTL = timedelta(hours=24)
IDEMPOTENCY_KEY_PATTERN = re.compile(r"[A-Za-z0-9_-]{16,64}")
# Execuções aceitas que ainda não começaram, somando todas as contas (1.7, "Processamento").
MAX_QUEUED_RUNS = 10
NOTIFY_CHANNEL = "goblinshift_runs"
# Chaves de advisory lock na forma de dois int4 (classe, objeto). A classe separa os usos deste
# módulo de qualquer outro advisory lock do banco; 0x6773 é "gs" em ASCII.
ADVISORY_CLASS_QUEUE = 0x6773_0001
ADVISORY_CLASS_IDEMPOTENCY = 0x6773_0002
QUEUE_LOCK_OBJECT = 0
_ACTIVE_RUN_PREDICATE = "status IN ('queued', 'running', 'stopping')"
_CANONICAL_SEPARATORS = (",", ":")


class AcceptanceError(Exception):
    """Base das recusas da aceitação."""


class IdempotencyKeyInvalidError(AcceptanceError):
    """Idempotency-Key ausente ou fora de 16 a 64 caracteres `[A-Za-z0-9_-]` (→ 400)."""


class InvalidRunCommandError(AcceptanceError):
    """Comando semanticamente inválido (→ 422 `VALIDATION_FAILED`), com um erro por campo.

    `location` é o caminho com pontos a partir da raiz do comando, como `parameters.filters.0`.
    """

    def __init__(self, errors: Sequence[FieldError]) -> None:
        self.errors = tuple(errors)
        super().__init__("; ".join(f"{e.location}: {e.message}" for e in self.errors))


class IdempotencyKeyReusedError(AcceptanceError):
    """Mesma chave vigente com outro comando (→ 422 `IDEMPOTENCY_KEY_REUSED`)."""


class IdempotencyInProgressError(AcceptanceError):
    """A requisição original com esta chave ainda está em andamento (→ 409)."""


class QueueFullError(AcceptanceError):
    """Fila global cheia ou ocupada além do `lock_timeout` (→ 503 `QUEUE_FULL`)."""


class ActiveRunLimitError(AcceptanceError):
    """A conta já tem uma execução não terminal (→ 409 `ACTIVE_RUN_LIMIT`)."""


@dataclass(frozen=True)
class RunCommand:
    """Comando como o cliente enviou; `parameters` é JSON ainda não validado."""

    recipe_id: str
    recipe_version: int
    input_upload_ids: tuple[UUID, ...]
    parameters: object


@dataclass(frozen=True)
class ValidatedRun:
    """Comando com os defaults da versão aplicados, em forma canônica."""

    recipe: RecipeVersion
    input_upload_ids: tuple[UUID, ...]
    parameters: dict[str, object]

    @property
    def command_hash(self) -> bytes:
        return command_hash(
            self.recipe.recipe_id, self.recipe.version, self.parameters, self.input_upload_ids
        )


@dataclass(frozen=True)
class Accepted:
    run_id: UUID
    replayed: bool
    idempotency_expires_at: datetime


def parse_idempotency_key(raw: str | None) -> str:
    if raw is None or IDEMPOTENCY_KEY_PATTERN.fullmatch(raw) is None:
        raise IdempotencyKeyInvalidError(
            "Idempotency-Key deve ter de 16 a 64 caracteres [A-Za-z0-9_-]"
        )
    return raw


def command_hash(
    recipe_id: str,
    recipe_version: int,
    parameters: object,
    input_upload_ids: Sequence[UUID],
) -> bytes:
    """SHA-256 do JSON canônico: chaves ordenadas, sem espaços, listas na ordem recebida (1.6).

    Só é estável porque os defaults nunca mudam dentro de uma `recipe_version`.
    """
    canonical = json.dumps(
        {
            "input_upload_ids": [str(upload_id) for upload_id in input_upload_ids],
            "parameters": parameters,
            "recipe_id": recipe_id,
            "recipe_version": recipe_version,
        },
        sort_keys=True,
        separators=_CANONICAL_SEPARATORS,
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).digest()


def validate_run(command: RunCommand) -> ValidatedRun:
    """Valida o que não depende do banco: versão, parâmetros e forma das entradas.

    Serve ao `validate_only`, que não exige uploads: aqui a lista pode vir vazia, mas nunca com
    repetição nem acima de `max_inputs`. Versão retirada levanta `RecipeVersionRetiredError`.
    """
    try:
        recipe = CATALOG.get_version(command.recipe_id, command.recipe_version)
    except UnknownRecipeVersionError:
        _refuse("recipe_version", "versão de receita desconhecida")
    if recipe.status is VersionStatus.RETIRED:
        raise RecipeVersionRetiredError(f"{recipe.recipe_id} v{recipe.version}")
    try:
        parameters = recipe.resolve(command.parameters)
    except InvalidParametersError as error:
        raise InvalidRunCommandError(
            [_under("parameters", field_error) for field_error in error.errors]
        ) from None
    _check_input_shape(command.input_upload_ids, recipe.limits["max_inputs"])
    return ValidatedRun(recipe, command.input_upload_ids, parameters)


def accept_run(
    session: Session,
    owner_id: UUID,
    command: RunCommand,
    idem_key: str | None,
    traceparent: str | None,
    *,
    executor_image: str,
) -> Accepted:
    """Aceita e enfileira a execução, ou devolve a já aceita com a mesma chave (replay).

    `executor_image` é a imagem do executor deste deploy (`GS_EXECUTOR_IMAGE`), fixada na Run
    para que todas as tentativas usem a mesma, mesmo depois de um deploy novo. Abre e confirma
    a própria transação: quando devolve, a Run e o NOTIFY já estão confirmados.
    """
    key = parse_idempotency_key(idem_key)
    validated = validate_run(command)
    if not validated.input_upload_ids:
        _refuse("input_upload_ids", "informe ao menos um upload")
    digest = validated.command_hash
    with session.begin():
        _lock_idempotency_key(session, owner_id, key)
        replay = _find_live_record(session, owner_id, key, digest)
        if replay is not None:
            return replay
        _lock_global_queue(session)
        run_id = _insert_run(session, owner_id, validated, traceparent, executor_image)
        uploads = lock_ready_uploads(session, owner_id, validated.input_upload_ids)
        session.execute(
            insert(run_input),
            [
                {"run_id": run_id, "position": position, "owner_id": owner_id, "upload_id": u}
                for position, u in enumerate(validated.input_upload_ids)
            ],
        )
        objects.hold(session, [u.object_id for u in uploads], HolderKind.RUN, run_id)
        expires_at = _record_key(session, owner_id, key, digest, run_id)
        session.execute(select(func.pg_notify(NOTIFY_CHANNEL, "")))
    return Accepted(run_id=run_id, replayed=False, idempotency_expires_at=expires_at)


def _check_input_shape(upload_ids: Sequence[UUID], max_inputs: int) -> None:
    if len(set(upload_ids)) != len(upload_ids):
        _refuse("input_upload_ids", "a lista não aceita IDs repetidos")
    if len(upload_ids) > max_inputs:
        _refuse("input_upload_ids", f"a receita aceita no máximo {max_inputs} entradas")


def _lock_idempotency_key(session: Session, owner_id: UUID, key: str) -> None:
    """Sem espera: se outra transação tem a chave, a requisição original ainda está em curso.

    O objeto da trava é um hash de 32 bits da chave; uma colisão entre chaves diferentes em
    voo ao mesmo tempo só produz um 409 a mais, que o cliente repete após o Retry-After.
    """
    acquired = session.execute(
        select(
            func.pg_try_advisory_xact_lock(
                ADVISORY_CLASS_IDEMPOTENCY,
                func.hashtext(f"{owner_id}:{CREATE_RUN_OPERATION}:{key}"),
            )
        )
    ).scalar_one()
    if not acquired:
        raise IdempotencyInProgressError("outra requisição com esta chave está em andamento")


def _find_live_record(session: Session, owner_id: UUID, key: str, digest: bytes) -> Accepted | None:
    record = session.execute(
        select(
            idempotency_record.c.command_hash,
            idempotency_record.c.run_id,
            idempotency_record.c.expires_at,
        ).where(
            idempotency_record.c.owner_id == owner_id,
            idempotency_record.c.operation == CREATE_RUN_OPERATION,
            idempotency_record.c.idem_key == key,
            idempotency_record.c.expires_at > func.now(),
        )
    ).one_or_none()
    if record is None:
        return None
    if bytes(record.command_hash) != digest:
        raise IdempotencyKeyReusedError("a chave já foi usada com outro comando")
    return Accepted(run_id=record.run_id, replayed=True, idempotency_expires_at=record.expires_at)


def _lock_global_queue(session: Session) -> None:
    """Serializa as aceitações de todas as réplicas e confere a fila sob a trava."""
    try:
        session.execute(select(func.pg_advisory_xact_lock(ADVISORY_CLASS_QUEUE, QUEUE_LOCK_OBJECT)))
    except OperationalError as error:
        if isinstance(error.orig, psycopg.errors.LockNotAvailable):
            raise QueueFullError("fila global ocupada") from None
        raise
    waiting = session.execute(
        select(func.count()).select_from(run).where(run.c.status == Status.QUEUED)
    ).scalar_one()
    if waiting >= MAX_QUEUED_RUNS:
        raise QueueFullError("fila global cheia")


def _insert_run(
    session: Session,
    owner_id: UUID,
    validated: ValidatedRun,
    traceparent: str | None,
    executor_image: str,
) -> UUID:
    recipe = validated.recipe
    run_id: UUID | None = session.execute(
        pg_insert(run)
        .values(
            owner_id=owner_id,
            recipe_id=recipe.recipe_id,
            recipe_version=recipe.version,
            executor_digest=executor_image,
            parameters=validated.parameters,
            limits=dict(recipe.limits),
            status=Status.QUEUED,
            queue_expires_at=func.now() + QUEUE_WINDOW,
            traceparent=traceparent,
        )
        .on_conflict_do_nothing(
            index_elements=[run.c.owner_id], index_where=text(_ACTIVE_RUN_PREDICATE)
        )
        .returning(run.c.id)
    ).scalar_one_or_none()
    if run_id is None:
        raise ActiveRunLimitError("a conta já tem uma execução não terminal")
    return run_id


def _record_key(
    session: Session, owner_id: UUID, key: str, digest: bytes, run_id: UUID
) -> datetime:
    """Grava a chave, reaproveitando um registro só se ele já venceu."""
    statement = pg_insert(idempotency_record).values(
        owner_id=owner_id,
        operation=CREATE_RUN_OPERATION,
        idem_key=key,
        command_hash=digest,
        run_id=run_id,
        expires_at=func.now() + IDEMPOTENCY_TTL,
    )
    expires_at: datetime | None = session.execute(
        statement.on_conflict_do_update(
            index_elements=[
                idempotency_record.c.owner_id,
                idempotency_record.c.operation,
                idempotency_record.c.idem_key,
            ],
            set_={
                "command_hash": statement.excluded.command_hash,
                "run_id": statement.excluded.run_id,
                "created_at": func.now(),
                "expires_at": statement.excluded.expires_at,
            },
            where=idempotency_record.c.expires_at <= func.now(),
        ).returning(idempotency_record.c.expires_at)
    ).scalar_one_or_none()
    if expires_at is None:
        # Todo escritor da chave passa pela trava do passo 1, e o registro vigente já virou
        # replay; chegar aqui é um escritor fora do protocolo.
        raise RuntimeError("registro de idempotência vigente escrito sem a trava da chave")
    return expires_at


def _under(prefix: str, error: FieldError) -> FieldError:
    location = f"{prefix}.{error.location}" if error.location else prefix
    return FieldError(location, error.message)


def _refuse(location: str, message: str) -> NoReturn:
    raise InvalidRunCommandError([FieldError(location, message)])
