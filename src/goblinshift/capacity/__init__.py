"""Livro-razão de capacidade: reservas de armazenamento por conta e no ambiente (ADR 023).

Camada de baixo, usada por `files` (reserva do upload) e `runs` (teto de saída da Run). Fila
global, execução ativa por conta e transferências simultâneas continuam nos mecanismos de cada
módulo (advisory lock, índice único parcial e `transfer_slot`); este módulo cuida só do que é
quantidade reservável.

Contrato fixado na Fase 0 da fatia 6; a Frente D implementa os corpos. Cada função roda na
transação de quem chama. Ordem das travas: pool da conta e depois o global, sempre, para não
haver deadlock.
"""

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from sqlalchemy.orm import Session

GIB = 1024**3
# Tetos de 1.7; gravados no pool quando ele nasce (o global pela migração 0006).
GLOBAL_STORAGE_MAX_BYTES = 20 * GIB
ACCOUNT_STORAGE_MAX_BYTES = 4 * GIB


class Resource(StrEnum):
    STORAGE_BYTES = "storage_bytes"


class HolderKind(StrEnum):
    UPLOAD = "upload"
    RUN = "run"
    OBJECT = "object"


class AccountCapacityExceededError(Exception):
    """Reserva acima do teto da conta: 429 `QUOTA_EXCEEDED`, com `Retry-After`."""


class GlobalCapacityExceededError(Exception):
    """Reserva acima do teto do ambiente: 503 `SERVICE_UNAVAILABLE`, com `Retry-After`."""


@dataclass(frozen=True)
class CapacityUsage:
    storage_reserved_bytes: int
    storage_max_bytes: int


@dataclass(frozen=True)
class ReconcileReport:
    # Pools cujo `reserved` divergia da soma das reservas vivas e foi corrigido.
    pools_corrected: int
    # Objetos `committed` sem reserva (criados pela API anterior durante o deploy) adotados.
    objects_adopted: int


def reserve(
    session: Session,
    *,
    owner_id: UUID,
    holder: HolderKind,
    holder_id: UUID,
    resource: Resource,
    amount: int,
) -> None:
    """Reserva `amount` na conta e no ambiente, tudo ou nada; acima do teto levanta."""
    raise NotImplementedError("Frente D da fatia 6")


def release(session: Session, *, holder: HolderKind, holder_id: UUID) -> int:
    """Libera a reserva viva do detentor e devolve a quantidade; repetir devolve 0."""
    raise NotImplementedError("Frente D da fatia 6")


def transfer(
    session: Session,
    *,
    from_holder: tuple[HolderKind, UUID],
    to_holder: tuple[HolderKind, UUID],
    amount: int,
) -> None:
    """Passa `amount` da reserva de um detentor para outro, sem tocar os pools."""
    raise NotImplementedError("Frente D da fatia 6")


def usage(session: Session, owner_id: UUID) -> CapacityUsage:
    raise NotImplementedError("Frente D da fatia 6")


def reconcile(session: Session, *, limit: int) -> ReconcileReport:
    """Recalcula os pools a partir do livro-razão e adota objetos sem reserva, em lote."""
    raise NotImplementedError("Frente D da fatia 6")


def purge_owner(session: Session, owner_id: UUID) -> None:
    """Apaga o pool e as reservas já liberadas da conta, antes da purga de `app_user`.

    Chamada por `identity.purge` (Frente C) depois que todos os objetos da conta foram
    removidos; reserva ainda viva é estado impossível nesse ponto e levanta erro.
    """
    raise NotImplementedError("Frente D da fatia 6")
