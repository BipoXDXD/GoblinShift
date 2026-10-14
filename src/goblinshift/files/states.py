"""Estados e papéis gravados nas tabelas de `files` (os mesmos valores dos CHECKs de 3.3)."""

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


class Availability(StrEnum):
    """Se o conteúdo ainda pode ser usado, independente do estado histórico (1.7).

    Não é gravada: deriva de `deleted_at` e `expires_at`. Exclusão vence expiração, porque foi
    um ato do dono.
    """

    AVAILABLE = "available"
    EXPIRED = "expired"
    DELETED = "deleted"

    @classmethod
    def of(cls, *, deleted: bool, expired: bool) -> Availability:
        if deleted:
            return cls.DELETED
        if expired:
            return cls.EXPIRED
        return cls.AVAILABLE


class TransferDirection(StrEnum):
    """Envio e download têm vagas separadas (1.7: 2 uploads e 2 downloads por conta)."""

    UPLOAD = "upload"
    DOWNLOAD = "download"
