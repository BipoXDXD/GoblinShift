"""Livro-razão de capacidade: reservas de armazenamento por conta e no ambiente (ADR 024).

Camada de baixo, usada por `files` (reserva do upload) e `runs` (teto de saída da Run). Fila
global, execução ativa por conta e transferências simultâneas continuam nos mecanismos de cada
módulo (advisory lock, índice único parcial e `transfer_slot`); este módulo cuida só do que é
quantidade reservável.

Cada função roda na transação de quem chama. Ordem das travas: pool da conta e depois o global,
sempre, para não haver deadlock.
"""

from goblinshift.capacity.ledger import (
    ACCOUNT_STORAGE_MAX_BYTES,
    GIB,
    GLOBAL_STORAGE_MAX_BYTES,
    RELEASED_RETENTION,
    AccountCapacityExceededError,
    CapacityUsage,
    GlobalCapacityExceededError,
    GlobalUsage,
    HolderKind,
    InsufficientReservationError,
    ReservationNotFoundError,
    Resource,
    StorageBelowReservedError,
    delete_released_reservations,
    global_usage,
    has_live_reservation,
    purge_owner,
    release,
    release_many,
    reserve,
    reserve_many,
    set_account_storage_max,
    transfer,
    usage,
)
from goblinshift.capacity.reconciliation import ReconcileReport, Unreserved, reconcile

__all__ = [
    "ACCOUNT_STORAGE_MAX_BYTES",
    "GIB",
    "GLOBAL_STORAGE_MAX_BYTES",
    "RELEASED_RETENTION",
    "AccountCapacityExceededError",
    "CapacityUsage",
    "GlobalCapacityExceededError",
    "GlobalUsage",
    "HolderKind",
    "InsufficientReservationError",
    "ReconcileReport",
    "ReservationNotFoundError",
    "Resource",
    "StorageBelowReservedError",
    "Unreserved",
    "delete_released_reservations",
    "global_usage",
    "has_live_reservation",
    "purge_owner",
    "reconcile",
    "release",
    "release_many",
    "reserve",
    "reserve_many",
    "set_account_storage_max",
    "transfer",
    "usage",
]
