"""Tabelas de arquivos (docs/GoblinShiftPlan.md, 3.3 e 3.6).

`stored_object` é o arquivo físico; `upload` e `artifact` o referenciam por FK composta com o
proprietário, de modo que o banco impede ligar recursos de contas diferentes.
"""

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    Table,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)

from goblinshift.platform.db import metadata

stored_object = Table(
    "stored_object",
    metadata,
    Column("id", Uuid, primary_key=True, server_default=func.uuidv7()),
    Column("owner_id", Uuid, ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=False),
    Column("storage_key", Text, nullable=False, unique=True),
    Column("state", Text, nullable=False, server_default="pending"),
    # Conhecidos só depois que o conteúdo foi gravado inteiro.
    Column("sha256", LargeBinary(32)),
    Column("size_bytes", BigInteger),
    # Candidatos (saídas antes da publicação) guardam a tentativa e a geração que os criou.
    Column("attempt_id", Uuid),
    Column("generation", Integer),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("committed_at", DateTime(timezone=True)),
    Column("delete_requested_at", DateTime(timezone=True)),
    UniqueConstraint("owner_id", "id"),
    Index(None, "owner_id"),
    Index(
        "stored_object_delete_requested_idx",
        "delete_requested_at",
        postgresql_where=text("state = 'delete_requested'"),
    ),
    CheckConstraint(
        "state IN ('pending', 'committed', 'delete_requested', 'deleted', 'missing')", name="state"
    ),
    CheckConstraint(
        "state = 'pending' OR (sha256 IS NOT NULL AND size_bytes IS NOT NULL)",
        name="content_known",
    ),
    CheckConstraint("size_bytes >= 0", name="size_bytes"),
    CheckConstraint("octet_length(sha256) = 32", name="sha256"),
)

upload = Table(
    "upload",
    metadata,
    Column("id", Uuid, primary_key=True, server_default=func.uuidv7()),
    Column("owner_id", Uuid, nullable=False),
    Column("object_id", Uuid, nullable=False, unique=True),
    # Dado do usuário, nunca caminho (3.6). Anulado 30 dias depois de criado, só depois da
    # exclusão (1.7, "Retenção"; migração 0010).
    Column("original_name", Text),
    Column("expected_size", BigInteger, nullable=False),
    Column("state", Text, nullable=False, server_default="created"),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("ready_at", DateTime(timezone=True)),
    # Retenção de 1.7: 1 h sem atividade enquanto incompleto, 24 h sem uso depois de pronto.
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("deleted_at", DateTime(timezone=True)),
    UniqueConstraint("owner_id", "id"),
    ForeignKeyConstraint(
        ["owner_id", "object_id"],
        ["stored_object.owner_id", "stored_object.id"],
        ondelete="RESTRICT",
    ),
    Index(None, "owner_id", "created_at"),
    Index("upload_expires_at_idx", "expires_at", postgresql_where=text("deleted_at IS NULL")),
    CheckConstraint("state IN ('created', 'receiving', 'ready', 'failed')", name="state"),
    CheckConstraint("expected_size >= 0", name="expected_size"),
    CheckConstraint("length(original_name) BETWEEN 1 AND 255", name="original_name"),
    CheckConstraint(
        "original_name IS NOT NULL OR deleted_at IS NOT NULL", name="name_until_deleted"
    ),
    # Uploads excluídos que a retenção ainda vai anular (1.7).
    Index(
        "upload_anonymize_idx",
        "created_at",
        postgresql_where=text("original_name IS NOT NULL AND deleted_at IS NOT NULL"),
    ),
)

artifact = Table(
    "artifact",
    metadata,
    Column("id", Uuid, primary_key=True, server_default=func.uuidv7()),
    Column("owner_id", Uuid, nullable=False),
    Column("object_id", Uuid, nullable=False),
    Column("role", Text, nullable=False),
    # Nome final da saída (regras de 3.6, já aplicadas pela receita), usado no download.
    Column("file_name", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("deleted_at", DateTime(timezone=True)),
    # Só no preview: a saída de conteúdo de que ele deriva (GET /artifacts/{id}/preview).
    Column("source_artifact_id", Uuid),
    UniqueConstraint("owner_id", "id"),
    ForeignKeyConstraint(
        ["owner_id", "object_id"],
        ["stored_object.owner_id", "stored_object.id"],
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["owner_id", "source_artifact_id"],
        ["artifact.owner_id", "artifact.id"],
        # O preview não existe sem a saída de origem: a purga e a retenção a removem junto.
        ondelete="CASCADE",
    ),
    Index(None, "object_id"),
    Index(
        "artifact_source_artifact_id_idx",
        "source_artifact_id",
        postgresql_where=text("source_artifact_id IS NOT NULL"),
    ),
    CheckConstraint("octet_length(file_name) BETWEEN 1 AND 240", name="file_name"),
    Index("artifact_expires_at_idx", "expires_at", postgresql_where=text("deleted_at IS NULL")),
    CheckConstraint("role IN ('output', 'report', 'manifest', 'package', 'preview')", name="role"),
    CheckConstraint("(role = 'preview') = (source_artifact_id IS NOT NULL)", name="preview_source"),
)

object_use = Table(
    "object_use",
    metadata,
    Column("id", Uuid, primary_key=True, server_default=func.uuidv7()),
    Column("object_id", Uuid, ForeignKey("stored_object.id", ondelete="RESTRICT"), nullable=False),
    Column("holder_kind", Text, nullable=False),
    Column("holder_id", Uuid, nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("ended_at", DateTime(timezone=True)),
    # Um detentor segura cada objeto no máximo uma vez ao mesmo tempo: `hold` repetido é no-op.
    Index(
        "object_use_open_idx",
        "object_id",
        "holder_kind",
        "holder_id",
        unique=True,
        postgresql_where=text("ended_at IS NULL"),
    ),
    Index(None, "holder_kind", "holder_id"),
    CheckConstraint("holder_kind IN ('run', 'download')", name="holder_kind"),
)

# Vagas de transferência simultânea (1.7). Ficam no banco, e não na memória do processo, para
# que o limite valha entre réplicas da API. Cada linha é uma transferência em andamento.
transfer_slot = Table(
    "transfer_slot",
    metadata,
    Column("id", Uuid, primary_key=True, server_default=func.uuidv7()),
    Column("owner_id", Uuid, ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=False),
    Column("direction", Text, nullable=False),
    Column("acquired_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    # Vencida, a vaga não conta mais e é removida pela próxima reserva da mesma direção.
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Index(None, "owner_id"),
    CheckConstraint("direction IN ('upload', 'download')", name="direction"),
)
