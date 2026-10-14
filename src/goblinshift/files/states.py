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
