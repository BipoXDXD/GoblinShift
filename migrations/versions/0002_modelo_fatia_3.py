"""Modelo da fatia 3: contas, chaves, arquivos e execuções.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-13 23:00:25.520435

Lembrete (docs/GoblinShiftPlan.md, 3.8): expand/contract. Esta migração só acrescenta o
que o código anterior tolera; remoções vão numa migração de um deploy posterior.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "app_user",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("issuer", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default="active", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("status IN ('active', 'blocked')", name=op.f("app_user_status_check")),
        sa.CheckConstraint(
            "length(display_name) BETWEEN 1 AND 200", name=op.f("app_user_display_name_check")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("app_user_pkey")),
        sa.UniqueConstraint("issuer", "subject", name=op.f("app_user_issuer_subject_key")),
    )
    op.create_table(
        "api_key",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("public_id", sa.Text(), nullable=False),
        sa.Column("secret_hash", sa.LargeBinary(length=32), nullable=False),
        sa.Column("scopes", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("cardinality(scopes) > 0", name=op.f("api_key_scopes_check")),
        sa.CheckConstraint("expires_at > created_at", name=op.f("api_key_expires_at_check")),
        sa.CheckConstraint(
            "octet_length(secret_hash) = 32", name=op.f("api_key_secret_hash_check")
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["app_user.id"], name=op.f("api_key_owner_id_fkey"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("api_key_pkey")),
        sa.UniqueConstraint("public_id", name=op.f("api_key_public_id_key")),
    )
    op.create_index(op.f("api_key_owner_id_idx"), "api_key", ["owner_id"], unique=False)
    op.create_table(
        "run",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("recipe_id", sa.Text(), nullable=False),
        sa.Column("recipe_version", sa.Integer(), nullable=False),
        sa.Column("executor_digest", sa.Text(), nullable=False),
        sa.Column("parameters", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("limits", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.Text(), server_default="queued", nullable=False),
        sa.Column("phase", sa.Text(), nullable=True),
        sa.Column("generation", sa.Integer(), server_default="0", nullable=False),
        sa.Column("claim_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "eligible_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("queue_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stop_reason", sa.Text(), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("traceparent", sa.Text(), nullable=True),
        sa.Column("summary", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(status = 'running') = (phase IS NOT NULL)", name=op.f("run_phase_only_running_check")
        ),
        sa.CheckConstraint(
            "(status IN ('succeeded', 'succeeded_with_warnings', 'canceled', 'failed')) "
            "= (finished_at IS NOT NULL)",
            name=op.f("run_finished_at_terminal_check"),
        ),
        sa.CheckConstraint(
            "phase IN ('inspecting', 'processing', 'packaging', 'finalizing', 'retry_wait')",
            name=op.f("run_phase_check"),
        ),
        sa.CheckConstraint(
            "status <> 'failed' OR error_code IS NOT NULL", name=op.f("run_failed_has_code_check")
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'stopping', 'succeeded', 'succeeded_with_warnings', "
            "'canceled', 'failed')",
            name=op.f("run_status_check"),
        ),
        sa.CheckConstraint("generation >= 0 AND claim_count >= 0", name=op.f("run_counters_check")),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["app_user.id"], name=op.f("run_owner_id_fkey"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("run_pkey")),
        sa.UniqueConstraint("owner_id", "id", name=op.f("run_owner_id_id_key")),
    )
    op.create_index(
        "run_claimable_idx",
        "run",
        ["created_at", "id"],
        unique=False,
        postgresql_where=sa.text("status = 'queued' OR phase = 'retry_wait'"),
    )
    op.create_index(
        "run_one_active_per_owner_idx",
        "run",
        ["owner_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running', 'stopping')"),
    )
    op.create_index(
        "run_owner_history_idx",
        "run",
        ["owner_id", sa.literal_column("created_at DESC"), sa.literal_column("id DESC")],
        unique=False,
    )
    op.create_table(
        "stored_object",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), server_default="pending", nullable=False),
        sa.Column("sha256", sa.LargeBinary(length=32), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("attempt_id", sa.Uuid(), nullable=True),
        sa.Column("generation", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delete_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "state = 'pending' OR (sha256 IS NOT NULL AND size_bytes IS NOT NULL)",
            name=op.f("stored_object_content_known_check"),
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'committed', 'delete_requested', 'deleted', 'missing')",
            name=op.f("stored_object_state_check"),
        ),
        sa.CheckConstraint("octet_length(sha256) = 32", name=op.f("stored_object_sha256_check")),
        sa.CheckConstraint("size_bytes >= 0", name=op.f("stored_object_size_bytes_check")),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["app_user.id"],
            name=op.f("stored_object_owner_id_fkey"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("stored_object_pkey")),
        sa.UniqueConstraint("owner_id", "id", name=op.f("stored_object_owner_id_id_key")),
        sa.UniqueConstraint("storage_key", name=op.f("stored_object_storage_key_key")),
    )
    op.create_index(
        "stored_object_delete_requested_idx",
        "stored_object",
        ["delete_requested_at"],
        unique=False,
        postgresql_where=sa.text("state = 'delete_requested'"),
    )
    op.create_index(op.f("stored_object_owner_id_idx"), "stored_object", ["owner_id"], unique=False)
    op.create_table(
        "artifact",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("object_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("file_name", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "octet_length(file_name) BETWEEN 1 AND 240", name=op.f("artifact_file_name_check")
        ),
        sa.CheckConstraint("role IN ('output', 'report')", name=op.f("artifact_role_check")),
        sa.ForeignKeyConstraint(
            ["owner_id", "object_id"],
            ["stored_object.owner_id", "stored_object.id"],
            name=op.f("artifact_owner_id_object_id_fkey"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("artifact_pkey")),
        sa.UniqueConstraint("owner_id", "id", name=op.f("artifact_owner_id_id_key")),
    )
    op.create_index(
        "artifact_expires_at_idx",
        "artifact",
        ["expires_at"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(op.f("artifact_object_id_idx"), "artifact", ["object_id"], unique=False)
    op.create_table(
        "attempt",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("container_name", sa.Text(), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("executor_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.CheckConstraint("number BETWEEN 1 AND 4", name=op.f("attempt_number_check")),
        sa.ForeignKeyConstraint(
            ["run_id"], ["run.id"], name=op.f("attempt_run_id_fkey"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("attempt_pkey")),
        sa.UniqueConstraint("container_name", name=op.f("attempt_container_name_key")),
        sa.UniqueConstraint("run_id", "generation", name=op.f("attempt_run_id_generation_key")),
        sa.UniqueConstraint("run_id", "number", name=op.f("attempt_run_id_number_key")),
    )
    op.create_index(
        "attempt_lease_idx",
        "attempt",
        ["lease_expires_at"],
        unique=False,
        postgresql_where=sa.text("ended_at IS NULL"),
    )
    op.create_index(
        "attempt_one_alive_per_run_idx",
        "attempt",
        ["run_id"],
        unique=True,
        postgresql_where=sa.text("ended_at IS NULL"),
    )
    op.create_table(
        "idempotency_record",
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("operation", sa.Text(), nullable=False),
        sa.Column("idem_key", sa.Text(), nullable=False),
        sa.Column("command_hash", sa.LargeBinary(length=32), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "idem_key ~ '^[A-Za-z0-9_-]{16,64}$'", name=op.f("idempotency_record_idem_key_check")
        ),
        sa.CheckConstraint(
            "octet_length(command_hash) = 32", name=op.f("idempotency_record_command_hash_check")
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "run_id"],
            ["run.owner_id", "run.id"],
            name=op.f("idempotency_record_owner_id_run_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["app_user.id"],
            name=op.f("idempotency_record_owner_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "owner_id", "operation", "idem_key", name=op.f("idempotency_record_pkey")
        ),
    )
    op.create_index(
        op.f("idempotency_record_expires_at_idx"),
        "idempotency_record",
        ["expires_at"],
        unique=False,
    )
    op.create_index(
        op.f("idempotency_record_owner_id_run_id_idx"),
        "idempotency_record",
        ["owner_id", "run_id"],
        unique=False,
    )
    op.create_table(
        "object_use",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("object_id", sa.Uuid(), nullable=False),
        sa.Column("holder_kind", sa.Text(), nullable=False),
        sa.Column("holder_id", sa.Uuid(), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "holder_kind IN ('run', 'download')", name=op.f("object_use_holder_kind_check")
        ),
        sa.ForeignKeyConstraint(
            ["object_id"],
            ["stored_object.id"],
            name=op.f("object_use_object_id_fkey"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("object_use_pkey")),
    )
    op.create_index(
        op.f("object_use_holder_kind_holder_id_idx"),
        "object_use",
        ["holder_kind", "holder_id"],
        unique=False,
    )
    op.create_index(
        "object_use_open_idx",
        "object_use",
        ["object_id", "holder_kind", "holder_id"],
        unique=True,
        postgresql_where=sa.text("ended_at IS NULL"),
    )
    op.create_table(
        "upload",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("object_id", sa.Uuid(), nullable=False),
        sa.Column("original_name", sa.Text(), nullable=False),
        sa.Column("expected_size", sa.BigInteger(), nullable=False),
        sa.Column("state", sa.Text(), server_default="created", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "state IN ('created', 'receiving', 'ready', 'failed')", name=op.f("upload_state_check")
        ),
        sa.CheckConstraint("expected_size >= 0", name=op.f("upload_expected_size_check")),
        sa.CheckConstraint(
            "length(original_name) BETWEEN 1 AND 255", name=op.f("upload_original_name_check")
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "object_id"],
            ["stored_object.owner_id", "stored_object.id"],
            name=op.f("upload_owner_id_object_id_fkey"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("upload_pkey")),
        sa.UniqueConstraint("object_id", name=op.f("upload_object_id_key")),
        sa.UniqueConstraint("owner_id", "id", name=op.f("upload_owner_id_id_key")),
    )
    op.create_index(
        "upload_expires_at_idx",
        "upload",
        ["expires_at"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        op.f("upload_owner_id_created_at_idx"), "upload", ["owner_id", "created_at"], unique=False
    )
    op.create_table(
        "run_input",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("upload_id", sa.Uuid(), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.CheckConstraint("outcome IN ('ok', 'rejected')", name=op.f("run_input_outcome_check")),
        sa.CheckConstraint("position >= 0", name=op.f("run_input_position_check")),
        sa.ForeignKeyConstraint(
            ["owner_id", "run_id"],
            ["run.owner_id", "run.id"],
            name=op.f("run_input_owner_id_run_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "upload_id"],
            ["upload.owner_id", "upload.id"],
            name=op.f("run_input_owner_id_upload_id_fkey"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("run_id", "position", name=op.f("run_input_pkey")),
        sa.UniqueConstraint("run_id", "upload_id", name=op.f("run_input_run_id_upload_id_key")),
    )
    op.create_index(
        op.f("run_input_owner_id_upload_id_idx"),
        "run_input",
        ["owner_id", "upload_id"],
        unique=False,
    )
    op.create_table(
        "run_output",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("artifact_id", sa.Uuid(), nullable=False),
        sa.Column("logical_name", sa.Text(), nullable=False),
        sa.CheckConstraint("position >= 0", name=op.f("run_output_position_check")),
        sa.ForeignKeyConstraint(
            ["owner_id", "artifact_id"],
            ["artifact.owner_id", "artifact.id"],
            name=op.f("run_output_owner_id_artifact_id_fkey"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "run_id"],
            ["run.owner_id", "run.id"],
            name=op.f("run_output_owner_id_run_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("run_id", "position", name=op.f("run_output_pkey")),
        sa.UniqueConstraint("artifact_id", name=op.f("run_output_artifact_id_key")),
        sa.UniqueConstraint(
            "run_id", "logical_name", name=op.f("run_output_run_id_logical_name_key")
        ),
    )


def downgrade() -> None:
    op.drop_table("run_output")
    op.drop_index(op.f("run_input_owner_id_upload_id_idx"), table_name="run_input")
    op.drop_table("run_input")
    op.drop_index(op.f("upload_owner_id_created_at_idx"), table_name="upload")
    op.drop_index(
        "upload_expires_at_idx", table_name="upload", postgresql_where=sa.text("deleted_at IS NULL")
    )
    op.drop_table("upload")
    op.drop_index(
        "object_use_open_idx", table_name="object_use", postgresql_where=sa.text("ended_at IS NULL")
    )
    op.drop_index(op.f("object_use_holder_kind_holder_id_idx"), table_name="object_use")
    op.drop_table("object_use")
    op.drop_index(op.f("idempotency_record_owner_id_run_id_idx"), table_name="idempotency_record")
    op.drop_index(op.f("idempotency_record_expires_at_idx"), table_name="idempotency_record")
    op.drop_table("idempotency_record")
    op.drop_index(
        "attempt_one_alive_per_run_idx",
        table_name="attempt",
        postgresql_where=sa.text("ended_at IS NULL"),
    )
    op.drop_index(
        "attempt_lease_idx", table_name="attempt", postgresql_where=sa.text("ended_at IS NULL")
    )
    op.drop_table("attempt")
    op.drop_index(op.f("artifact_object_id_idx"), table_name="artifact")
    op.drop_index(
        "artifact_expires_at_idx",
        table_name="artifact",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_table("artifact")
    op.drop_index(op.f("stored_object_owner_id_idx"), table_name="stored_object")
    op.drop_index(
        "stored_object_delete_requested_idx",
        table_name="stored_object",
        postgresql_where=sa.text("state = 'delete_requested'"),
    )
    op.drop_table("stored_object")
    op.drop_index("run_owner_history_idx", table_name="run")
    op.drop_index(
        "run_one_active_per_owner_idx",
        table_name="run",
        postgresql_where=sa.text("status IN ('queued', 'running', 'stopping')"),
    )
    op.drop_index(
        "run_claimable_idx",
        table_name="run",
        postgresql_where=sa.text("status = 'queued' OR phase = 'retry_wait'"),
    )
    op.drop_table("run")
    op.drop_index(op.f("api_key_owner_id_idx"), table_name="api_key")
    op.drop_table("api_key")
    op.drop_table("app_user")
