"""Read a retained PostgreSQL slice through an explicitly authorized borrowed connection.

Receipt checksums identify supplied bytes, not the authority of their author. The caller
must authenticate/review them and lend one exclusive, idle, autocommit connection. This
module never connects, discovers credentials, closes the connection, or emits a seed.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import Protocol, cast

_SHA256 = re.compile(r"[0-9a-f]{64}")
_COMMIT = re.compile(r"[0-9a-f]{40}")
_SCHEMA = re.compile(r"[a-z_][a-z0-9_]{0,62}")
_REFERENCE = re.compile(r"[a-z0-9][a-z0-9._/:-]{0,254}@sha256:[0-9a-f]{64}")
_FIELDS = {
    "run": ("id", "executor_digest", "status", "phase"),
    "attempt": ("run_id", "ended_at", "executor_started_at"),
}
_MAX_BYTES = 8 * 1024 * 1024
_MAX_RECEIPT = 64 * 1024
_COUNTS = (
    "runs",
    "attempts",
    "unresolved",
    "ended",
    "physical",
    "never_started",
    "unresolved_physical",
    "runs_without_attempt",
    "invalid_refs",
    "invalid_states",
    "orphan_attempts",
    "multiple_unresolved",
    "reference_state_groups",
    "references",
    "grouped_runs",
    "grouped_attempts",
    "grouped_unresolved",
    "grouped_ended",
    "grouped_physical",
    "grouped_never_started",
    "grouped_unresolved_physical",
    "grouped_without_attempt",
)
_ROW_COUNTS = (
    "runs",
    "runs_without_attempt",
    "attempts",
    "unresolved",
    "ended",
    "physical",
    "never_started",
    "unresolved_physical",
)
_STATES = frozenset(
    {
        "queued",
        "running",
        "stopping",
        "succeeded",
        "succeeded_with_warnings",
        "canceled",
        "failed",
    }
)
_PHASES = frozenset({"inspecting", "processing", "packaging", "finalizing", "retry_wait"})
_ERROR_CODES = frozenset(
    {
        "invalid_authority",
        "connection_not_idle",
        "invalid_connection_mode",
        "identity_mismatch",
        "invalid_snapshot",
        "output_limit",
        "rollback_failed",
        "collection_failed",
    }
)


class InventoryError(ValueError):
    """Public fixed error code; driver/receipt/private identity details never escape."""


class ResultOrigins(Protocol):
    def ftable(self, column: int) -> int: ...


class Cursor(Protocol):
    @property
    def pgresult(self) -> ResultOrigins | None: ...
    def execute(self, query: str, parameters: tuple[object, ...] = ()) -> object: ...
    def fetchone(self) -> Sequence[object] | None: ...
    def __enter__(self) -> Cursor: ...
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None: ...


class ConnectionInfo(Protocol):
    @property
    def transaction_status(self) -> int: ...


class Connection(Protocol):
    @property
    def autocommit(self) -> bool: ...
    @property
    def info(self) -> ConnectionInfo: ...
    def cursor(self) -> AbstractContextManager[Cursor]: ...
    def rollback(self) -> None: ...


@dataclass(frozen=True)
class Receipt:
    document: bytes = field(repr=False)
    sha256: str


@dataclass(frozen=True)
class Relation:
    name: str
    oid: int
    fields: tuple[str, ...]


@dataclass(frozen=True)
class InventoryAuthority:
    authorization: Receipt
    cut: Receipt
    expected_database: str = field(repr=False)
    expected_role: str = field(repr=False)
    schema: str
    relations: tuple[Relation, ...]
    release_commit: str
    consumer_sha256: str
    executor_reference: str


def _receipt_valid(receipt: Receipt) -> bool:
    return (
        isinstance(receipt, Receipt)
        and isinstance(receipt.document, bytes)
        and 0 < len(receipt.document) <= _MAX_RECEIPT
        and isinstance(receipt.sha256, str)
        and _SHA256.fullmatch(receipt.sha256) is not None
        and hashlib.sha256(receipt.document).hexdigest() == receipt.sha256
    )


def _private_identifier_valid(value: str) -> bool:
    if not isinstance(value, str) or "\x00" in value:
        return False
    try:
        return 0 < len(value.encode("utf-8")) <= 63
    except UnicodeEncodeError:
        return False


def _check_authority(authority: InventoryAuthority) -> None:
    if not isinstance(authority, InventoryAuthority):
        raise InventoryError("invalid_authority")
    if not (
        _receipt_valid(authority.authorization)
        and _receipt_valid(authority.cut)
        and all(
            _private_identifier_valid(v)
            for v in (authority.expected_database, authority.expected_role)
        )
        and isinstance(authority.schema, str)
        and _SCHEMA.fullmatch(authority.schema)
        and isinstance(authority.release_commit, str)
        and _COMMIT.fullmatch(authority.release_commit)
        and isinstance(authority.consumer_sha256, str)
        and _SHA256.fullmatch(authority.consumer_sha256)
        and isinstance(authority.executor_reference, str)
        and _REFERENCE.fullmatch(authority.executor_reference)
        and isinstance(authority.relations, tuple)
        and len(authority.relations) == 2
        and all(
            isinstance(r, Relation)
            and type(r.oid) is int
            and 0 < r.oid < 2**32
            and isinstance(r.name, str)
            and r.name in _FIELDS
            and r.fields == _FIELDS[r.name]
            for r in authority.relations
        )
        and {r.name for r in authority.relations} == set(_FIELDS)
    ):
        raise InventoryError("invalid_authority")


_IDENTITY = """
SELECT current_database() = %s, current_user = %s,
       current_setting('transaction_isolation') = 'repeatable read',
       current_setting('transaction_read_only') = 'on',
       current_setting('row_security') = 'off',
       (SELECT c.oid = %s AND c.relkind IN ('r', 'p')
        FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON c.relnamespace = n.oid
        WHERE n.nspname = %s AND c.relname = 'run'),
       (SELECT c.oid = %s AND c.relkind IN ('r', 'p')
        FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON c.relnamespace = n.oid
        WHERE n.nspname = %s AND c.relname = 'attempt')
"""


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise InventoryError("invalid_snapshot")
        result[key] = value
    return result


def _reject_constant(value: str) -> object:
    raise InventoryError("invalid_snapshot")


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise InventoryError("invalid_snapshot")
    return cast(dict[str, object], value)


def _list(value: object) -> list[object]:
    if not isinstance(value, list):
        raise InventoryError("invalid_snapshot")
    return cast(list[object], value)


def _integer(value: object) -> int:
    if type(value) is not int or value < 0:
        raise InventoryError("invalid_snapshot")
    return value


def _state(row: dict[str, object]) -> tuple[str, str | None]:
    status, phase = row.get("status"), row.get("phase")
    if not isinstance(status, str) or status not in _STATES:
        raise InventoryError("invalid_snapshot")
    if status == "running":
        if not isinstance(phase, str) or phase not in _PHASES:
            raise InventoryError("invalid_snapshot")
    elif phase is not None:
        raise InventoryError("invalid_snapshot")
    return status, phase


def _validate(raw: object) -> dict[str, object]:
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > _MAX_BYTES:
        raise InventoryError("output_limit")
    report = _object(json.loads(raw, object_pairs_hook=_unique, parse_constant=_reject_constant))
    if (
        report.get("status") != "DB_SLICE_ONLY_UNVERIFIED"
        or report.get("ready_for_seed") is not False
        or report.get("transaction_isolation") != "repeatable read"
        or report.get("transaction_read_only") != "on"
    ):
        raise InventoryError("invalid_snapshot")
    counts = _object(report.get("counts"))
    if set(counts) != {*_COUNTS, "reconciled"} or counts["reconciled"] is not True:
        raise InventoryError("invalid_snapshot")
    values = {name: _integer(counts[name]) for name in _COUNTS}
    if any(
        values[k]
        for k in ("invalid_refs", "invalid_states", "orphan_attempts", "multiple_unresolved")
    ):
        raise InventoryError("invalid_snapshot")
    rows = _list(report.get("reference_states"))
    if len(rows) != values["reference_state_groups"] or len(rows) > 4096:
        raise InventoryError("invalid_snapshot")
    totals = dict.fromkeys(_ROW_COUNTS, 0)
    references: set[str] = set()
    seen: set[tuple[str, str, str | None]] = set()
    states: dict[tuple[str, str | None], int] = {}
    for value in rows:
        row = _object(value)
        if set(row) != {*_ROW_COUNTS, "reference", "status", "phase"}:
            raise InventoryError("invalid_snapshot")
        ref = row["reference"]
        if not isinstance(ref, str) or not _REFERENCE.fullmatch(ref):
            raise InventoryError("invalid_snapshot")
        status, phase = _state(row)
        key = (ref, status, phase)
        if key in seen:
            raise InventoryError("invalid_snapshot")
        seen.add(key)
        references.add(ref)
        for name in _ROW_COUNTS:
            totals[name] += _integer(row[name])
        states[(status, phase)] = states.get((status, phase), 0) + _integer(row["runs"])
    if len(references) != values["references"] or any(totals[k] != values[k] for k in totals):
        raise InventoryError("invalid_snapshot")
    state_totals: dict[tuple[str, str | None], int] = {}
    for value in _list(report.get("run_states")):
        row = _object(value)
        state_key = _state(row)
        if set(row) != {"status", "phase", "runs"} or state_key in state_totals:
            raise InventoryError("invalid_snapshot")
        state_totals[state_key] = _integer(row["runs"])
    if state_totals != states:
        raise InventoryError("invalid_snapshot")
    for name in _ROW_COUNTS:
        grouped = "grouped_without_attempt" if name == "runs_without_attempt" else f"grouped_{name}"
        if values[grouped] != values[name]:
            raise InventoryError("invalid_snapshot")
    return report


def _read(connection: Connection, authority: InventoryAuthority) -> tuple[dict[str, object], str]:
    if connection.info.transaction_status != 0:
        raise InventoryError("connection_not_idle")
    if connection.autocommit is not True:
        raise InventoryError("invalid_connection_mode")
    sql_bytes = Path(__file__).with_name("snapshot.sql").read_bytes()
    sql = sql_bytes.decode("utf-8").replace("__SCHEMA__", f'"{authority.schema}"')
    own_transaction = False
    try:
        with connection.cursor() as cursor:
            own_transaction = True
            cursor.execute("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            cursor.execute("SET LOCAL search_path = pg_catalog")
            cursor.execute("SET LOCAL row_security = off")
            cursor.execute("SET LOCAL statement_timeout = '30s'")
            cursor.execute("SET LOCAL lock_timeout = '2s'")
            cursor.execute("SET LOCAL idle_in_transaction_session_timeout = '35s'")
            # Hold the same read locks as the inventory SELECT before checking its OIDs.
            names = f'"{authority.schema}"."run", "{authority.schema}"."attempt"'
            cursor.execute("LOCK TABLE " + names + " IN ACCESS SHARE MODE")
            relations = {r.name: r for r in authority.relations}
            cursor.execute(
                _IDENTITY,
                (
                    authority.expected_database,
                    authority.expected_role,
                    relations["run"].oid,
                    authority.schema,
                    relations["attempt"].oid,
                    authority.schema,
                ),
            )
            identity = cursor.fetchone()
            if identity is None or len(identity) != 7 or any(v is not True for v in identity):
                raise InventoryError("identity_mismatch")
            cursor.execute(sql)
            # These origins belong to the actual query's CTEs, even for empty tables.
            # A catalog check under RepeatableRead alone can miss a schema-name swap.
            origins = cursor.pgresult
            if (
                origins is None
                or origins.ftable(1) != relations["run"].oid
                or origins.ftable(2) != relations["attempt"].oid
            ):
                raise InventoryError("identity_mismatch")
            row = cursor.fetchone()
            if (
                row is None
                or len(row) != 3
                or row[1] is not None
                or row[2] is not None
                or cursor.fetchone() is not None
            ):
                raise InventoryError("invalid_snapshot")
            report = _validate(row[0])
    finally:
        if own_transaction:
            try:
                connection.rollback()
                if connection.info.transaction_status != 0:
                    raise InventoryError("rollback_failed")
            except Exception:
                raise InventoryError("rollback_failed") from None
    return report, hashlib.sha256(sql_bytes).hexdigest()


def collect(
    provider: Callable[[], AbstractContextManager[Connection]],
    authority: InventoryAuthority,
) -> dict[str, object]:
    """Consume only the caller's explicit capability; return no operational approval.

    The provider owns connection lifetime. It must lend exclusive use and authenticate
    its authorization/cut receipts. No identity data or exception from it is published.
    """
    _check_authority(authority)
    payload: tuple[dict[str, object], str] | None = None
    try:
        with provider() as connection:
            payload = _read(connection, authority)
    except InventoryError as error:
        # A provider can raise this public exception type too; never trust its message.
        code = error.args[0] if len(error.args) == 1 else None
        safe = code if isinstance(code, str) and code in _ERROR_CODES else "collection_failed"
        raise InventoryError(safe) from None
    except Exception:
        raise InventoryError("collection_failed") from None
    if payload is None:
        # Context managers are allowed to suppress exceptions; suppression is not success.
        raise InventoryError("collection_failed")
    report, sql_sha256 = payload
    result: dict[str, object] = {
        "snapshot_valid": True,
        "retained_database_all_complete": True,
        "all_complete": False,
        "origin_refs_verified": False,
        "active_reader_verified": False,
        "seed_ready": False,
        "identity_matches": True,
        "operator_supplied_bindings": {
            "authorization_receipt_sha256": authority.authorization.sha256,
            "cut_receipt_sha256": authority.cut.sha256,
            "release_commit": authority.release_commit,
            "consumer_sha256": authority.consumer_sha256,
            "executor_reference": authority.executor_reference,
        },
        "executed_sql_source_sha256": sql_sha256,
        "inventory": report,
    }
    if len(json.dumps(result).encode("utf-8")) > _MAX_BYTES:
        raise InventoryError("output_limit")
    return result
