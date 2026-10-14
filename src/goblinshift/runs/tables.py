"""Tabelas de execuções (docs/GoblinShiftPlan.md, 3.3 a 3.5). A própria `run` é a fila."""

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    PrimaryKeyConstraint,
    Table,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

from goblinshift.platform.db import metadata

_ACTIVE = "status IN ('queued', 'running', 'stopping')"

run = Table(
    "run",
    metadata,
    Column("id", Uuid, primary_key=True, server_default=func.uuidv7()),
    Column("owner_id", Uuid, ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=False),
    Column("recipe_id", Text, nullable=False),
    Column("recipe_version", Integer, nullable=False),
    Column("executor_digest", Text, nullable=False),
    # Parâmetros com defaults aplicados e limites de 1.7, fixados na aceitação.
    Column("parameters", JSONB, nullable=False),
    Column("limits", JSONB, nullable=False),
    Column("status", Text, nullable=False, server_default="queued"),
    Column("phase", Text),
    # Token de fencing: só muda no claim (3.4).
    Column("generation", Integer, nullable=False, server_default="0"),
    Column("claim_count", Integer, nullable=False, server_default="0"),
    Column("eligible_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("queue_expires_at", DateTime(timezone=True), nullable=False),
    Column("deadline_at", DateTime(timezone=True)),
    Column("cancel_requested_at", DateTime(timezone=True)),
    # A primeira razão de parada persistida decide canceled/failed (3.5).
    Column("stop_reason", Text),
    Column("error_code", Text),
    Column("traceparent", Text),
    Column("summary", JSONB),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("started_at", DateTime(timezone=True)),
    Column("finished_at", DateTime(timezone=True)),
    UniqueConstraint("owner_id", "id"),
    Index("run_owner_history_idx", "owner_id", text("created_at DESC"), text("id DESC")),
    # Fila: só as linhas que um claim pode pegar, na ordem do claim.
    Index(
        "run_claimable_idx",
        "created_at",
        "id",
        postgresql_where=text("status = 'queued' OR phase = 'retry_wait'"),
    ),
    # Uma execução não terminal por conta (1.7), garantida pelo banco e não por "checa e insere".
    Index("run_one_active_per_owner_idx", "owner_id", unique=True, postgresql_where=text(_ACTIVE)),
    CheckConstraint(
        "status IN ('queued', 'running', 'stopping', 'succeeded', 'succeeded_with_warnings', "
        "'canceled', 'failed')",
        name="status",
    ),
    CheckConstraint(
        "phase IN ('inspecting', 'processing', 'packaging', 'finalizing', 'retry_wait')",
        name="phase",
    ),
    CheckConstraint("(status = 'running') = (phase IS NOT NULL)", name="phase_only_running"),
    CheckConstraint(
        "(status IN ('succeeded', 'succeeded_with_warnings', 'canceled', 'failed')) "
        "= (finished_at IS NOT NULL)",
        name="finished_at_terminal",
    ),
    CheckConstraint("status <> 'failed' OR error_code IS NOT NULL", name="failed_has_code"),
    CheckConstraint("generation >= 0 AND claim_count >= 0", name="counters"),
)

run_input = Table(
    "run_input",
    metadata,
    Column("run_id", Uuid, nullable=False),
    Column("position", Integer, nullable=False),
    Column("owner_id", Uuid, nullable=False),
    Column("upload_id", Uuid, nullable=False),
    Column("outcome", Text),
    Column("error_code", Text),
    PrimaryKeyConstraint("run_id", "position"),
    UniqueConstraint("run_id", "upload_id"),
    ForeignKeyConstraint(["owner_id", "run_id"], ["run.owner_id", "run.id"], ondelete="CASCADE"),
    ForeignKeyConstraint(
        ["owner_id", "upload_id"], ["upload.owner_id", "upload.id"], ondelete="RESTRICT"
    ),
    Index(None, "owner_id", "upload_id"),
    CheckConstraint("position >= 0", name="position"),
    CheckConstraint("outcome IN ('ok', 'rejected')", name="outcome"),
)

attempt = Table(
    "attempt",
    metadata,
    Column("id", Uuid, primary_key=True, server_default=func.uuidv7()),
    Column("run_id", Uuid, ForeignKey("run.id", ondelete="CASCADE"), nullable=False),
    Column("number", Integer, nullable=False),
    Column("generation", Integer, nullable=False),
    Column("container_name", Text, nullable=False, unique=True),
    Column("lease_expires_at", DateTime(timezone=True), nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    # Só conta como tentativa física (no máximo 2) a que chegou a pôr o executor para trabalhar;
    # um claim cujo lease venceu antes disso reagenda sem gastá-la (3.4).
    Column("executor_started_at", DateTime(timezone=True)),
    Column("ended_at", DateTime(timezone=True)),
    Column("error_code", Text),
    UniqueConstraint("run_id", "number"),
    UniqueConstraint("run_id", "generation"),
    # Nunca duas tentativas vivas por execução, mesmo diante de um bug (3.4).
    Index(
        "attempt_one_alive_per_run_idx",
        "run_id",
        unique=True,
        postgresql_where=text("ended_at IS NULL"),
    ),
    Index("attempt_lease_idx", "lease_expires_at", postgresql_where=text("ended_at IS NULL")),
    # Uma linha por claim; o quinto claim vira INFRA_POISON em vez de nova tentativa (3.4).
    CheckConstraint("number BETWEEN 1 AND 4", name="number"),
)

run_output = Table(
    "run_output",
    metadata,
    Column("run_id", Uuid, nullable=False),
    Column("position", Integer, nullable=False),
    Column("owner_id", Uuid, nullable=False),
    Column("artifact_id", Uuid, nullable=False, unique=True),
    Column("logical_name", Text, nullable=False),
    PrimaryKeyConstraint("run_id", "position"),
    UniqueConstraint("run_id", "logical_name"),
    ForeignKeyConstraint(["owner_id", "run_id"], ["run.owner_id", "run.id"], ondelete="CASCADE"),
    ForeignKeyConstraint(
        ["owner_id", "artifact_id"], ["artifact.owner_id", "artifact.id"], ondelete="RESTRICT"
    ),
    CheckConstraint("position >= 0", name="position"),
)

idempotency_record = Table(
    "idempotency_record",
    metadata,
    Column("owner_id", Uuid, ForeignKey("app_user.id", ondelete="CASCADE"), nullable=False),
    Column("operation", Text, nullable=False),
    Column("idem_key", Text, nullable=False),
    Column("command_hash", LargeBinary(32), nullable=False),
    Column("run_id", Uuid, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    PrimaryKeyConstraint("owner_id", "operation", "idem_key"),
    ForeignKeyConstraint(["owner_id", "run_id"], ["run.owner_id", "run.id"], ondelete="CASCADE"),
    Index(None, "expires_at"),
    Index(None, "owner_id", "run_id"),
    CheckConstraint("idem_key ~ '^[A-Za-z0-9_-]{16,64}$'", name="idem_key"),
    CheckConstraint("octet_length(command_hash) = 32", name="command_hash"),
)
