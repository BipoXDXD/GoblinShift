"""Estados e papéis gravados nas tabelas de `files` (os mesmos valores dos CHECKs de 3.3)."""

from datetime import datetime
from enum import StrEnum


class UploadState(StrEnum):
    CREATED = "created"
    RECEIVING = "receiving"
    READY = "ready"
    FAILED = "failed"


class ObjectState(StrEnum):
    PENDING = "pending"
    COMMITTED = "committed"
    DELETE_REQUESTED = "delete_requested"
    DELETED = "deleted"
    MISSING = "missing"


class HolderKind(StrEnum):
    RUN = "run"
    DOWNLOAD = "download"


class ArtifactRole(StrEnum):
    OUTPUT = "output"
    REPORT = "report"
    # Fatia 5 (ADR 018): `manifest.json` e `<recipe_id>.zip` do empacotador comum.
    MANIFEST = "manifest"
    PACKAGE = "package"


class Availability(StrEnum):
    """Se o conteúdo ainda pode ser usado, independente do estado histórico (1.7).

    Não é gravada: deriva de `deleted_at` e `expires_at`. A retenção também grava `deleted_at`
    ao expirar, então a causa vem da ordem: `deleted_at` antes de `expires_at` é exclusão pedida
    pelo dono, que vence a expiração; a partir de `expires_at`, o recurso venceu por retenção.
    """

    AVAILABLE = "available"
    EXPIRED = "expired"
    DELETED = "deleted"

    @classmethod
    def of(
        cls, *, deleted_at: datetime | None, expires_at: datetime, expired: bool
    ) -> Availability:
        """`expired` é `expires_at <= now()` calculado pelo banco, a mesma hora de quem consulta."""
        if deleted_at is not None and deleted_at < expires_at:
            return cls.DELETED
        if expired or deleted_at is not None:
            return cls.EXPIRED
        return cls.AVAILABLE


class TransferDirection(StrEnum):
    """Envio e download têm vagas separadas (1.7: 2 uploads e 2 downloads por conta)."""

    UPLOAD = "upload"
    DOWNLOAD = "download"
