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
   inserir sem trava seria write skew). A espera obedece ao `lock_timeout` da conexão. As cotas
   diárias da conta (30 execuções e 2 GiB de entrada em 24 h) são contadas sob a mesma trava.
4. Capacidade da conta: o `INSERT` da Run com `ON CONFLICT DO NOTHING` sobre o índice único
   parcial `run_one_active_per_owner_idx`; sem linha devolvida, já há uma execução ativa.
5. Uploads (`FOR SHARE`), bytes de entrada contra a cota diária, `run_input` na ordem e usos dos
   objetos.
6. Reserva do teto de saída no livro-razão (`capacity`, pool da conta e depois o global, por
   `UPDATE` condicional). Fica por último entre as travas: nada espera com os pools travados.
7. Registro de idempotência (`ON CONFLICT … DO UPDATE … WHERE expires_at <= now()`, reuso só
   depois de vencido) e `NOTIFY goblinshift_runs` no mesmo commit: gravar a Run já é enfileirar.

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

from goblinshift import capacity
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
from goblinshift.files.uploads import ReadyUpload, lock_ready_uploads
from goblinshift.processing.executor.report_file import REPORT_MAX_BYTES
from goblinshift.processing.output_dir import DEFAULT_MAX_BYTES
from goblinshift.processing.packaging import PackageLimits
from goblinshift.runs.lifecycle import QUEUE_WINDOW, Status
from goblinshift.runs.quotas import (
    MAX_INPUT_BYTES_PER_WINDOW,
    MAX_RUNS_PER_WINDOW,
    DailyUsage,
    daily_usage,
)
from goblinshift.runs.store import db_now
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
_PACKAGE_LIMITS = PackageLimits()
# Teto de saída de uma Run (1.7): conteúdo, relatório, manifesto e ZIP. Reservado na aceitação
# e convertido em reservas dos objetos publicados (ADR 024, D-5); cerca de 1 GiB.
RUN_OUTPUT_CEILING_BYTES = (
    DEFAULT_MAX_BYTES
    + REPORT_MAX_BYTES
    + _PACKAGE_LIMITS.manifest_max_bytes
    + _PACKAGE_LIMITS.package_max_bytes
)
# Versões que limitam a soma das entradas (o Detox copia cada entrada aceita para a saída).
_MAX_TOTAL_INPUT_BYTES = "max_total_input_bytes"
# Teto de cada entrada, que toda versão declara: 32 MiB, ou 256 MiB no Media Mill (1.7, ADR 036).
# O envio dá o teto de vídeo a qualquer nome terminado em .mp4, .mov ou .webm; a extensão não é
# fronteira, então a aceitação confere o tamanho guardado contra a receita escolhida.
_MAX_INPUT_BYTES = "max_input_bytes"
_MIN_RETRY_AFTER = timedelta(seconds=1)
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


class DailyQuotaExceededError(AcceptanceError):
    """Cota diária de execuções ou de bytes de entrada (→ 429 `QUOTA_EXCEEDED`).

    `retry_after` é o tempo até a Run mais antiga da janela sair dela.
    """

    def __init__(self, message: str, retry_after: timedelta) -> None:
        super().__init__(message)
        self.retry_after = retry_after


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

    Sem espaço para o teto de saída levanta `capacity.AccountCapacityExceededError` ou
    `capacity.GlobalCapacityExceededError`, com a transação desfeita.
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
        now = db_now(session)
        usage = daily_usage(session, owner_id, now)
        _check_run_quota(usage, now)
        run_id = _insert_run(session, owner_id, validated, traceparent, executor_image)
        uploads = lock_ready_uploads(session, owner_id, validated.input_upload_ids)
        _check_input_bytes(validated.recipe, uploads, usage, now)
        session.execute(
            insert(run_input),
            [
                {"run_id": run_id, "position": position, "owner_id": owner_id, "upload_id": u}
                for position, u in enumerate(validated.input_upload_ids)
            ],
        )
        objects.hold(session, [u.object_id for u in uploads], HolderKind.RUN, run_id)
        capacity.reserve(
            session,
            owner_id=owner_id,
            holder=capacity.HolderKind.RUN,
            holder_id=run_id,
            resource=capacity.Resource.STORAGE_BYTES,
            amount=RUN_OUTPUT_CEILING_BYTES,
        )
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


def _check_run_quota(usage: DailyUsage, now: datetime) -> None:
    if usage.runs >= MAX_RUNS_PER_WINDOW:
        raise DailyQuotaExceededError(
            f"a conta já criou {usage.runs} execuções nas últimas 24 h",
            _until_window_frees(usage, now),
        )


def _check_input_bytes(
    recipe: RecipeVersion, uploads: Sequence[ReadyUpload], usage: DailyUsage, now: datetime
) -> None:
    """Teto da versão e cota diária de entrada; uma execução maior que a cota é inválida."""
    file_max = recipe.limits[_MAX_INPUT_BYTES]
    if any(u.size_bytes > file_max for u in uploads):
        _refuse("input_upload_ids", f"cada entrada pode ter até {file_max} bytes")
    incoming = sum(u.size_bytes for u in uploads)
    version_max = recipe.limits.get(_MAX_TOTAL_INPUT_BYTES)
    if version_max is not None and incoming > version_max:
        _refuse("input_upload_ids", f"as entradas somam mais de {version_max} bytes")
    if incoming > MAX_INPUT_BYTES_PER_WINDOW:
        _refuse(
            "input_upload_ids",
            f"as entradas somam mais que a cota diária de {MAX_INPUT_BYTES_PER_WINDOW} bytes",
        )
    if usage.input_bytes + incoming > MAX_INPUT_BYTES_PER_WINDOW:
        raise DailyQuotaExceededError(
            "a entrada passa da cota diária de bytes", _until_window_frees(usage, now)
        )


def _until_window_frees(usage: DailyUsage, now: datetime) -> timedelta:
    resets_at = usage.resets_at()
    if resets_at is None:
        raise RuntimeError("cota diária esgotada sem execução na janela")
    return max(resets_at - now, _MIN_RETRY_AFTER)


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
