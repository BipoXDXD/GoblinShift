"""Identidade web e cotas (fatia 6): sessões, estado do login, auditoria e livro-razão.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-14 18:00:00.000000

Lembrete (docs/GoblinShiftPlan.md, 3.8): expand/contract. Só acréscimos: colunas anuláveis em
`app_user` e tabelas novas que o código da fatia 5 ignora. O pool global de armazenamento nasce
aqui com o teto de 1.7 (20 GiB); uploads criados pela API anterior durante o deploy ficam sem
reserva e são adotados pela reconciliação da manutenção (ADR 023).
"""

from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Repetido de propósito: a migração não importa o código da aplicação, que muda depois dela.
GLOBAL_STORAGE_MAX_BYTES = 20 * 1024**3
AUDIT_EVENT_TYPES = (
    "LOGIN_SUCCEEDED",
    "LOGIN_FAILED",
    "LOGOUT",
    "LOGOUT_ALL",
    "KEY_ISSUED",
    "KEY_REVOKED",
    "REAUTH_REQUIRED",
    "CSRF_FAILED",
    "ACCESS_DENIED",
    "ACCOUNT_DELETION_REQUESTED",
    "ACCOUNT_BLOCKED",
    "ACCOUNT_UNBLOCKED",
)


def _timestamp(name: str, *, nullable: bool = False, now: bool = False) -> sa.Column[datetime]:
    default = sa.text("now()") if now else None
    return sa.Column(name, sa.DateTime(timezone=True), server_default=default, nullable=nullable)


def upgrade() -> None:
    _expand_app_user()
    _create_web_session()
    _create_login_state()
    _create_audit_event()
    _create_capacity_pool()
    _create_resource_reservation()


def _expand_app_user() -> None:
    op.add_column("app_user", sa.Column("terms_version", sa.Text(), nullable=True))
    op.add_column("app_user", _timestamp("terms_accepted_at", nullable=True))
    op.add_column("app_user", _timestamp("deletion_requested_at", nullable=True))
    op.create_check_constraint(
        op.f("app_user_terms_check"),
        "app_user",
        "(terms_version IS NULL) = (terms_accepted_at IS NULL)",
    )
    op.create_check_constraint(
        op.f("app_user_terms_version_check"),
        "app_user",
        "length(terms_version) BETWEEN 1 AND 64",
    )
    op.create_index(
        op.f("app_user_deletion_requested_at_idx"),
        "app_user",
        ["deletion_requested_at"],
        postgresql_where=sa.text("deletion_requested_at IS NOT NULL"),
    )


def _create_web_session() -> None:
    op.create_table(
        "web_session",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("secret_hash", sa.LargeBinary(length=32), nullable=False),
        _timestamp("created_at", now=True),
        _timestamp("authenticated_at"),
        _timestamp("last_seen_at"),
        _timestamp("idle_expires_at"),
        _timestamp("absolute_expires_at"),
        _timestamp("revoked_at", nullable=True),
        sa.CheckConstraint(
            "octet_length(secret_hash) = 32", name=op.f("web_session_secret_hash_check")
        ),
        sa.CheckConstraint(
            "absolute_expires_at > authenticated_at",
            name=op.f("web_session_absolute_expires_at_check"),
        ),
        sa.CheckConstraint(
            "idle_expires_at <= absolute_expires_at",
            name=op.f("web_session_idle_expires_at_check"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["app_user.id"], name=op.f("web_session_user_id_fkey"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("web_session_pkey")),
        sa.UniqueConstraint("secret_hash", name=op.f("web_session_secret_hash_key")),
    )
    op.create_index(op.f("web_session_user_id_idx"), "web_session", ["user_id"])
    op.create_index(
        op.f("web_session_absolute_expires_at_idx"),
        "web_session",
        ["absolute_expires_at"],
        postgresql_where=sa.text("revoked_at IS NULL"),
    )


def _create_login_state() -> None:
    op.create_table(
        "login_state",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        _timestamp("expires_at"),
        sa.CheckConstraint("length(key) BETWEEN 1 AND 200", name=op.f("login_state_key_check")),
        sa.CheckConstraint("length(value) <= 4096", name=op.f("login_state_value_check")),
        sa.PrimaryKeyConstraint("key", name=op.f("login_state_pkey")),
    )
    op.create_index(op.f("login_state_expires_at_idx"), "login_state", ["expires_at"])


def _create_audit_event() -> None:
    event_types = ", ".join(f"'{event}'" for event in AUDIT_EVENT_TYPES)
    op.create_table(
        "audit_event",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        # Sem FK de propósito: o evento sobrevive à purga da conta (ADR 025).
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("ip_prefix", postgresql.CIDR(), nullable=True),
        _timestamp("occurred_at", now=True),
        sa.CheckConstraint(
            f"event_type IN ({event_types})", name=op.f("audit_event_event_type_check")
        ),
        sa.CheckConstraint(
            "outcome IN ('success', 'failure')", name=op.f("audit_event_outcome_check")
        ),
        sa.CheckConstraint(
            "masklen(ip_prefix) = CASE family(ip_prefix) WHEN 4 THEN 24 ELSE 48 END",
            name=op.f("audit_event_ip_prefix_check"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("audit_event_pkey")),
    )
    op.create_index(op.f("audit_event_occurred_at_idx"), "audit_event", ["occurred_at"])
    op.create_index(
        op.f("audit_event_user_id_occurred_at_idx"), "audit_event", ["user_id", "occurred_at"]
    )


def _create_capacity_pool() -> None:
    pool = op.create_table(
        "capacity_pool",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=True),
        sa.Column("resource", sa.Text(), nullable=False),
        sa.Column("max_amount", sa.BigInteger(), nullable=False),
        sa.Column("reserved", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.CheckConstraint(
            "scope IN ('global', 'account')", name=op.f("capacity_pool_scope_check")
        ),
        sa.CheckConstraint(
            "(scope = 'global') = (owner_id IS NULL)", name=op.f("capacity_pool_owner_check")
        ),
        sa.CheckConstraint(
            "resource IN ('storage_bytes')", name=op.f("capacity_pool_resource_check")
        ),
        sa.CheckConstraint(
            "reserved BETWEEN 0 AND max_amount", name=op.f("capacity_pool_reserved_check")
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["app_user.id"],
            name=op.f("capacity_pool_owner_id_fkey"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("capacity_pool_pkey")),
        sa.UniqueConstraint(
            "scope",
            "owner_id",
            "resource",
            name=op.f("capacity_pool_scope_owner_id_resource_key"),
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index(op.f("capacity_pool_owner_id_idx"), "capacity_pool", ["owner_id"])
    op.bulk_insert(
        pool,
        [
            {
                "scope": "global",
                "owner_id": None,
                "resource": "storage_bytes",
                "max_amount": GLOBAL_STORAGE_MAX_BYTES,
            }
        ],
    )


def _create_resource_reservation() -> None:
    op.create_table(
        "resource_reservation",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("holder_kind", sa.Text(), nullable=False),
        sa.Column("holder_id", sa.Uuid(), nullable=False),
        sa.Column("resource", sa.Text(), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        _timestamp("created_at", now=True),
        _timestamp("released_at", nullable=True),
        sa.CheckConstraint(
            "holder_kind IN ('upload', 'run', 'object')",
            name=op.f("resource_reservation_holder_kind_check"),
        ),
        sa.CheckConstraint(
            "resource IN ('storage_bytes')", name=op.f("resource_reservation_resource_check")
        ),
        sa.CheckConstraint("amount >= 0", name=op.f("resource_reservation_amount_check")),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["app_user.id"],
            name=op.f("resource_reservation_owner_id_fkey"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("resource_reservation_pkey")),
    )
    op.create_index(
        "resource_reservation_live_holder_idx",
        "resource_reservation",
        ["holder_kind", "holder_id", "resource"],
        unique=True,
        postgresql_where=sa.text("released_at IS NULL"),
    )
    op.create_index(op.f("resource_reservation_owner_id_idx"), "resource_reservation", ["owner_id"])


def downgrade() -> None:
    op.drop_table("resource_reservation")
    op.drop_table("capacity_pool")
    op.drop_table("audit_event")
    op.drop_table("login_state")
    op.drop_table("web_session")
    op.drop_index(op.f("app_user_deletion_requested_at_idx"), table_name="app_user")
    op.drop_constraint(op.f("app_user_terms_version_check"), "app_user", type_="check")
    op.drop_constraint(op.f("app_user_terms_check"), "app_user", type_="check")
    op.drop_column("app_user", "deletion_requested_at")
    op.drop_column("app_user", "terms_accepted_at")
    op.drop_column("app_user", "terms_version")
