"""Behavior against real PostgreSQL; no operational connection is discovered here."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager, suppress
from dataclasses import replace
from types import TracebackType
from typing import Any, Protocol

import psycopg
import pytest

from executor_inventory import reader as m


class PostgresFixture(Protocol):
    username: str
    password: str
    dbname: str

    def get_container_host_ip(self) -> str: ...
    def get_exposed_port(self, port: int) -> str | int: ...


Database = psycopg.Connection[tuple[Any, ...]]


def one(db: Database, query: str, parameters: tuple[object, ...] = ()) -> tuple[Any, ...]:
    row = db.execute(query, parameters).fetchone()
    assert row is not None
    return row


@pytest.fixture
def database(postgres: PostgresFixture) -> Iterator[Database]:
    with psycopg.connect(
        host=postgres.get_container_host_ip(),
        port=postgres.get_exposed_port(5432),
        dbname=postgres.dbname,
        user=postgres.username,
        password=postgres.password,
        autocommit=True,
    ) as db:
        db.execute("DROP SCHEMA IF EXISTS inventory_fixture CASCADE")
        db.execute("CREATE SCHEMA inventory_fixture")
        db.execute(
            "CREATE TABLE inventory_fixture.run (id integer PRIMARY KEY, "
            "executor_digest text, status text, phase text)"
        )
        db.execute(
            "CREATE TABLE inventory_fixture.attempt (id integer PRIMARY KEY, run_id integer, "
            "ended_at timestamptz, executor_started_at timestamptz)"
        )
        try:
            yield db
        finally:
            if db.info.transaction_status != 0:
                db.rollback()
            db.execute("DROP SCHEMA inventory_fixture CASCADE")


def authority(db: Database) -> m.InventoryAuthority:
    db_name, role = one(db, "SELECT current_database(), current_user")
    relations = []
    for name, fields in [
        ("run", ("id", "executor_digest", "status", "phase")),
        ("attempt", ("run_id", "ended_at", "executor_started_at")),
    ]:
        oid = one(db, "SELECT %s::regclass::oid", (f"inventory_fixture.{name}",))[0]
        relations.append(m.Relation(name=name, oid=oid, fields=fields))

    def receipt(document: bytes) -> m.Receipt:
        return m.Receipt(document=document, sha256=hashlib.sha256(document).hexdigest())

    return m.InventoryAuthority(
        authorization=receipt(b"test-only operator authorization, no production authority"),
        cut=receipt(b"test-only isolated database fixture cut"),
        expected_database=db_name,
        expected_role=role,
        schema="inventory_fixture",
        relations=tuple(relations),
        release_commit="a" * 40,
        consumer_sha256="b" * 64,
        executor_reference="ghcr.io/bipoxdxd/goblinshift-executor@sha256:" + "c" * 64,
    )


@contextmanager
def borrowed(db: Database) -> Iterator[m.Connection]:
    yield db


def collect(db: Database, auth: m.InventoryAuthority | None = None) -> dict[str, Any]:
    return m.collect(lambda: borrowed(db), authority(db) if auth is None else auth)


def test_missing_authority_never_calls_connection_provider() -> None:

    def forbidden() -> AbstractContextManager[m.Connection]:
        pytest.fail("provider called before checking mandatory authority")

    with pytest.raises(m.InventoryError, match=r"^invalid_authority$"):
        m.collect(forbidden, None)  # type: ignore[arg-type]


def test_empty_snapshot_cannot_become_seed_ready(database: Database) -> None:
    report = collect(database)
    assert report["snapshot_valid"] is True
    assert report["retained_database_all_complete"] is True
    assert report["all_complete"] is False
    assert report["origin_refs_verified"] is False
    assert report["active_reader_verified"] is False
    assert report["seed_ready"] is False
    assert report["inventory"]["counts"]["runs"] == 0
    assert report["inventory"]["reference_states"] == []
    assert database.closed is False
    assert database.info.transaction_status == 0
    assert one(database, "SHOW transaction_read_only")[0] == "off"
    encoded = json.dumps(report)
    assert "inventory_fixture_owner" not in encoded
    assert '"expected_database"' not in encoded
    assert '"expected_role"' not in encoded


def test_all_retained_states_and_attempts_are_reconciled(database: Database) -> None:
    ref_a = "ghcr.io/bipoxdxd/goblinshift-executor@sha256:" + "a" * 64
    # A second repository using the same digest must remain a separate reference.
    ref_b = "ghcr.io/other/approved-executor@sha256:" + "a" * 64
    for row in [
        (1, ref_a, "queued", None),
        (2, ref_a, "running", "retry_wait"),
        (3, ref_a, "stopping", None),
        (4, ref_b, "succeeded", None),
        (5, ref_b, "succeeded_with_warnings", None),
        (6, ref_b, "canceled", None),
        (7, ref_b, "failed", None),
    ]:
        database.execute("INSERT INTO inventory_fixture.run VALUES (%s,%s,%s,%s)", row)
    database.execute(
        "INSERT INTO inventory_fixture.attempt VALUES "
        "(1,2,now(),NULL),(2,2,NULL,NULL),(3,3,NULL,now()),(4,4,now(),now())"
    )
    report = collect(database)
    counts = report["inventory"]["counts"]
    assert {k: counts[k] for k in ["runs", "attempts", "unresolved", "ended", "physical"]} == {
        "runs": 7,
        "attempts": 4,
        "unresolved": 2,
        "ended": 2,
        "physical": 2,
    }
    assert counts["runs_without_attempt"] == 4
    assert counts["references"] == 2
    rows = report["inventory"]["reference_states"]
    queued = next(row for row in rows if row["status"] == "queued")
    assert queued["runs_without_attempt"] == 1
    retry = next(row for row in rows if row["phase"] == "retry_wait")
    assert retry["attempts"] == 2
    assert retry["unresolved"] == 1
    assert retry["physical"] == 0
    assert {r["reference"] for r in rows} == {ref_a, ref_b}
    assert report["all_complete"] is False


@pytest.mark.parametrize("mutation", ["invalid_ref", "invalid_state", "orphan", "two_alive"])
def test_inconsistent_database_never_returns_partial_references(
    database: Database,
    mutation: str,
) -> None:
    ref = "ghcr.io/bipoxdxd/goblinshift-executor@sha256:" + "d" * 64
    database.execute("INSERT INTO inventory_fixture.run VALUES (1,%s,'queued',NULL)", (ref,))
    if mutation == "invalid_ref":
        database.execute("UPDATE inventory_fixture.run SET executor_digest='PRIVATE-MALFORMED'")
    elif mutation == "invalid_state":
        database.execute("UPDATE inventory_fixture.run SET phase='PRIVATE-UNKNOWN'")
    elif mutation == "orphan":
        database.execute("INSERT INTO inventory_fixture.attempt VALUES (1,9,NULL,NULL)")
    else:
        database.execute(
            "INSERT INTO inventory_fixture.attempt VALUES (1,1,NULL,NULL),(2,1,NULL,NULL)"
        )
    with pytest.raises(m.InventoryError, match=r"^invalid_snapshot$") as error:
        collect(database)
    assert "PRIVATE" not in str(error.value)
    assert database.info.transaction_status == 0


def test_wrong_private_database_or_role_is_not_disclosed(database: Database) -> None:
    auth = replace(
        authority(database), expected_database="PRIVATE-DB", expected_role="PRIVATE-ROLE"
    )
    with pytest.raises(m.InventoryError, match=r"^identity_mismatch$") as error:
        collect(database, auth)
    assert "PRIVATE" not in str(error.value)
    assert database.info.transaction_status == 0


def test_wrong_relation_identity_is_rejected(database: Database) -> None:
    auth = authority(database)
    auth = replace(auth, relations=(replace(auth.relations[0], oid=1), auth.relations[1]))
    with pytest.raises(m.InventoryError, match=r"^identity_mismatch$"):
        collect(database, auth)


def test_inherited_transaction_is_never_rolled_back(database: Database) -> None:
    auth = authority(database)
    database.execute("BEGIN")
    database.execute("INSERT INTO inventory_fixture.run VALUES (1,'pending','queued',NULL)")
    with pytest.raises(m.InventoryError, match=r"^connection_not_idle$"):
        collect(database, auth)
    assert database.info.transaction_status != 0
    assert one(database, "SELECT count(*) FROM inventory_fixture.run")[0] == 1
    database.rollback()


def test_bad_receipt_and_extra_column_are_rejected_before_provider(database: Database) -> None:
    auth = authority(database)
    invalid = [
        replace(auth, authorization=replace(auth.authorization, sha256="0" * 64)),
        replace(
            auth,
            relations=(replace(auth.relations[0], fields=("id", "parameters")), auth.relations[1]),
        ),
        replace(auth, schema="inventory_fixture; SELECT secret"),
        replace(auth, expected_database="\ud800"),
    ]
    for item in invalid:

        def forbidden() -> AbstractContextManager[m.Connection]:
            pytest.fail("invalid authority opened provider")

        with pytest.raises(m.InventoryError, match=r"^invalid_authority$"):
            m.collect(forbidden, item)


class BoundaryCursor:
    """Only the external scheduling boundary is controlled; SQL still uses PostgreSQL."""

    def __init__(self, cursor: m.Cursor, after_identity: Any) -> None:
        self.cursor = cursor
        self.after_identity = after_identity

    @property
    def pgresult(self) -> m.ResultOrigins | None:
        return self.cursor.pgresult

    def execute(self, query: str, parameters: tuple[object, ...] = ()) -> object:
        return self.cursor.execute(query, parameters)

    def fetchone(self) -> Any:
        row = self.cursor.fetchone()
        if row is not None and len(row) == 7 and all(type(v) is bool for v in row):
            self.after_identity()
        return row

    def __enter__(self) -> BoundaryCursor:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        pass


class BoundaryConnection:
    def __init__(self, db: Database, after_identity: Any) -> None:
        self.db = db
        self.after_identity = after_identity

    @property
    def info(self) -> m.ConnectionInfo:
        return self.db.info

    @property
    def autocommit(self) -> bool:
        return self.db.autocommit

    @contextmanager
    def cursor(self) -> Iterator[m.Cursor]:
        with self.db.cursor() as cursor:
            yield BoundaryCursor(cursor, self.after_identity)

    def rollback(self) -> None:
        self.db.rollback()


def test_concurrent_commit_cannot_mix_counts_from_two_snapshots(
    database: Database,
    postgres: PostgresFixture,
) -> None:
    auth = authority(database)
    with psycopg.connect(
        host=postgres.get_container_host_ip(),
        port=postgres.get_exposed_port(5432),
        dbname=postgres.dbname,
        user=postgres.username,
        password=postgres.password,
        autocommit=True,
    ) as writer:

        def commit_after_snapshot() -> None:
            writer.execute(
                "INSERT INTO inventory_fixture.run VALUES "
                "(1,'ghcr.io/fixture/ref@sha256:' || repeat('f',64),'queued',NULL)"
            )

        @contextmanager
        def provider() -> Iterator[m.Connection]:
            yield BoundaryConnection(database, commit_after_snapshot)

        report = m.collect(provider, auth)
        snapshot = report["inventory"]
        assert isinstance(snapshot, dict)
        assert snapshot["counts"]["runs"] == 0
        assert snapshot["reference_states"] == []
        assert one(database, "SELECT count(*) FROM inventory_fixture.run")[0] == 1


@pytest.mark.parametrize("change", ["truncate", "table_swap", "schema_swap"])
def test_relation_identity_remains_bound_through_inventory_read(
    database: Database,
    postgres: PostgresFixture,
    change: str,
) -> None:
    auth = authority(database)
    database.execute(
        "INSERT INTO inventory_fixture.run VALUES "
        "(1,'ghcr.io/fixture/ref@sha256:' || repeat('f',64),'queued',NULL)"
    )
    blocked = []
    with psycopg.connect(
        host=postgres.get_container_host_ip(),
        port=postgres.get_exposed_port(5432),
        dbname=postgres.dbname,
        user=postgres.username,
        password=postgres.password,
        autocommit=True,
    ) as writer:
        writer.execute("SET lock_timeout = '100ms'")

        def change_after_identity() -> None:
            try:
                if change == "truncate":
                    writer.execute("TRUNCATE inventory_fixture.run")
                elif change == "table_swap":
                    writer.execute("ALTER TABLE inventory_fixture.run RENAME TO old_run")
                    writer.execute(
                        "CREATE TABLE inventory_fixture.run "
                        "(id integer, executor_digest text, status text, phase text)"
                    )
                else:
                    writer.execute("ALTER SCHEMA inventory_fixture RENAME TO old_inventory_fixture")
                    writer.execute("CREATE SCHEMA inventory_fixture")
                    writer.execute(
                        "CREATE TABLE inventory_fixture.run "
                        "(id integer, executor_digest text, status text, phase text)"
                    )
                    writer.execute(
                        "CREATE TABLE inventory_fixture.attempt "
                        "(id integer, run_id integer, ended_at timestamptz, "
                        "executor_started_at timestamptz)"
                    )
            except psycopg.errors.LockNotAvailable:
                blocked.append(True)

        @contextmanager
        def provider() -> Iterator[m.Connection]:
            yield BoundaryConnection(database, change_after_identity)

        try:
            if change == "schema_swap":
                # Table read locks cannot block a namespace rename; query-bound origins
                # must instead refuse its new empty tables, despite old catalog snapshot.
                with pytest.raises(m.InventoryError, match=r"^identity_mismatch$"):
                    m.collect(provider, auth)
                assert blocked == []
            else:
                report = m.collect(provider, auth)
                assert blocked == [True]
                snapshot = report["inventory"]
                assert isinstance(snapshot, dict)
                assert snapshot["counts"]["runs"] == 1
        finally:
            writer.execute("DROP SCHEMA IF EXISTS old_inventory_fixture CASCADE")


def test_extra_reference_groups_are_never_truncated(database: Database) -> None:
    database.execute(
        "INSERT INTO inventory_fixture.run "
        "SELECT n, 'ghcr.io/fixture/ref' || n::text || '@sha256:' || repeat('e',64), "
        "'queued', NULL FROM generate_series(1,4097) n"
    )
    with pytest.raises(m.InventoryError, match=r"^invalid_snapshot$"):
        collect(database)
    assert database.info.transaction_status == 0


def test_filtered_rls_population_is_error_not_empty_snapshot(database: Database) -> None:
    auth = authority(database)
    database.execute("CREATE ROLE inventory_fixture_reader NOLOGIN")
    try:
        database.execute("GRANT USAGE ON SCHEMA inventory_fixture TO inventory_fixture_reader")
        database.execute(
            "GRANT SELECT ON ALL TABLES IN SCHEMA inventory_fixture TO inventory_fixture_reader"
        )
        database.execute("ALTER TABLE inventory_fixture.run ENABLE ROW LEVEL SECURITY")
        database.execute("CREATE POLICY hide_all ON inventory_fixture.run USING (false)")
        database.execute("SET ROLE inventory_fixture_reader")
        auth = replace(auth, expected_role="inventory_fixture_reader")
        with pytest.raises(m.InventoryError, match=r"^collection_failed$"):
            collect(database, auth)
        assert database.info.transaction_status == 0
    finally:
        database.execute("RESET ROLE")
        database.execute(
            "REVOKE ALL ON ALL TABLES IN SCHEMA inventory_fixture FROM inventory_fixture_reader"
        )
        database.execute("REVOKE USAGE ON SCHEMA inventory_fixture FROM inventory_fixture_reader")
        database.execute("DROP ROLE inventory_fixture_reader")


def test_view_cannot_impersonate_reviewed_table(database: Database) -> None:
    database.execute("ALTER TABLE inventory_fixture.run RENAME TO original_run")
    database.execute(
        "CREATE VIEW inventory_fixture.run AS SELECT * FROM inventory_fixture.original_run"
    )
    # Even supplying the actual new OID cannot authorize a view in place of the source table.
    with pytest.raises(m.InventoryError, match=r"^identity_mismatch$"):
        collect(database)


def test_driver_or_provider_error_never_discloses_private_details(database: Database) -> None:
    auth = authority(database)

    def provider() -> AbstractContextManager[m.Connection]:
        raise RuntimeError("PRIVATE-DRIVER-CONNECTION-DETAIL")

    with pytest.raises(m.InventoryError, match=r"^collection_failed$") as error:
        m.collect(provider, auth)
    assert "PRIVATE" not in str(error.value)
    assert error.value.__suppress_context__ is True


def test_provider_cannot_smuggle_private_details_using_inventory_error(database: Database) -> None:
    auth = authority(database)

    def provider() -> AbstractContextManager[m.Connection]:
        raise m.InventoryError("PRIVATE-PROVIDER-DETAIL")

    with pytest.raises(m.InventoryError) as error:
        m.collect(provider, auth)
    assert str(error.value) == "collection_failed"


def test_suppressing_provider_cannot_skip_snapshot_validation(database: Database) -> None:
    auth = replace(authority(database), expected_role="PRIVATE-WRONG-ROLE")

    @contextmanager
    def provider() -> Iterator[m.Connection]:
        with suppress(m.InventoryError):
            yield database

    with pytest.raises(m.InventoryError, match=r"^collection_failed$"):
        m.collect(provider, auth)
    assert database.info.transaction_status == 0


def test_non_autocommit_connection_is_unchanged(database: Database) -> None:
    auth = authority(database)
    database.autocommit = False
    try:
        with pytest.raises(m.InventoryError, match=r"^invalid_connection_mode$"):
            collect(database, auth)
        assert database.info.transaction_status == 0
        assert database.autocommit is False
    finally:
        database.autocommit = True


def test_lock_timeout_rolls_back_only_own_transaction(
    database: Database,
    postgres: PostgresFixture,
) -> None:
    auth = authority(database)
    with psycopg.connect(
        host=postgres.get_container_host_ip(),
        port=postgres.get_exposed_port(5432),
        dbname=postgres.dbname,
        user=postgres.username,
        password=postgres.password,
        autocommit=True,
    ) as locker:
        locker.execute("BEGIN")
        locker.execute("LOCK TABLE inventory_fixture.run IN ACCESS EXCLUSIVE MODE")
        try:
            with pytest.raises(m.InventoryError, match=r"^collection_failed$"):
                collect(database, auth)
            assert database.info.transaction_status == 0
            assert database.closed is False
            assert locker.info.transaction_status != 0
        finally:
            locker.rollback()
