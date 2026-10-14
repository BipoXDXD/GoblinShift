"""Infra dos testes do backup externo: S3 local, healthchecks falso, bancos e chaves `age`.

O S3 é o gateway do SeaweedFS (as imagens do MinIO deixaram de ser publicadas), com três
identidades que imitam as chaves de aplicação do B2: a da VPS só escreve, a da restauração só
lê e lista, e a de administração cria o bucket e confere o que foi gravado.
"""

import base64
import hashlib
import json
import shutil
import subprocess
import threading
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from testcontainers.community.postgres import PostgresContainer
from testcontainers.core.container import DockerContainer

from goblinshift.identity import create_admin_user
from goblinshift.platform.db import make_engine, make_session_factory, unit_of_work
from goblinshift.platform.schema import read_code_revisions
from goblinshift.platform.settings import Settings
from tests.conftest import run_migrations
from tests.runs.support import insert_finished_run

ROOT = Path(__file__).resolve().parents[2]
BACKUP_SCRIPT = ROOT / "deploy" / "backup" / "pg-backup.sh"
RESTORE_SCRIPT = ROOT / "deploy" / "backup" / "pg-restore-check.sh"

SEAWEEDFS_IMAGE = (
    "chrislusf/seaweedfs:4.48"
    "@sha256:4e61d15fd35994cb1e43e1e553dff106794841fd9a99ade2fc8c8bfce4d7872d"
)
S3_PORT = 8333
S3_REGION = "us-east-1"
BUCKET = "gs-backups"
# Bucket sem Object Lock, para provar que a restauração recusa dump que pode ser sobrescrito.
UNLOCKED_BUCKET = "gs-backups-unlocked"
OBJECT_LOCK_30_DAYS = (
    "<ObjectLockConfiguration><ObjectLockEnabled>Enabled</ObjectLockEnabled><Rule>"
    "<DefaultRetention><Mode>COMPLIANCE</Mode><Days>30</Days></DefaultRetention>"
    "</Rule></ObjectLockConfiguration>"
)
S3_READY_TIMEOUT_S = 60.0
S3_READY_POLL_S = 0.5
HTTP_OK = 200


@dataclass(frozen=True)
class Credentials:
    key_id: str
    secret: str


ADMIN = Credentials("gs-admin", "admin-" + "a" * 32)
WRITER = Credentials("gs-writer", "writer-" + "w" * 32)
READER = Credentials("gs-reader", "reader-" + "r" * 32)


@dataclass(frozen=True)
class S3:
    endpoint: str

    def request(self, credentials: Credentials, *args: str) -> subprocess.CompletedProcess[bytes]:
        config = f'user = "{credentials.key_id}:{credentials.secret}"\n'
        return subprocess.run(
            [
                "curl",
                "-sS",
                "--aws-sigv4",
                f"aws:amz:{S3_REGION}:s3",
                "--config",
                "-",
                "-w",
                "\n%{http_code}",
                *args,
            ],
            input=config.encode(),
            capture_output=True,
            check=True,
        )

    def status(self, credentials: Credentials, *args: str) -> int:
        return int(self.request(credentials, *args).stdout.rsplit(b"\n", 1)[-1])

    def url(self, key: str = "") -> str:
        return f"{self.endpoint}/{BUCKET}/{key}"

    def keys(self, prefix: str) -> list[str]:
        out = self.request(ADMIN, f"{self.endpoint}/{BUCKET}?list-type=2&prefix={prefix}")
        body = out.stdout.rsplit(b"\n", 1)[0].decode()
        return sorted(part.split("</Key>")[0] for part in body.split("<Key>")[1:])

    def get(self, key: str) -> bytes:
        out = self.request(ADMIN, self.url(key))
        body, status = out.stdout.rsplit(b"\n", 1)
        assert int(status) == HTTP_OK, body
        return body

    def put(self, key: str, path: Path) -> None:
        assert self.status(ADMIN, "-T", str(path), "-o", "/dev/null", self.url(key)) == HTTP_OK


@pytest.fixture(scope="session")
def s3() -> Iterator[S3]:
    for tool in ("age", "age-keygen", "curl", "docker"):
        assert shutil.which(tool), f"o teste do backup precisa de `{tool}` no PATH"
    identities = {
        "identities": [
            {
                "name": "admin",
                "credentials": [{"accessKey": ADMIN.key_id, "secretKey": ADMIN.secret}],
                "actions": ["Admin", "Read", "Write", "List", "Tagging"],
            },
            {
                "name": "vps",
                "credentials": [{"accessKey": WRITER.key_id, "secretKey": WRITER.secret}],
                "actions": ["Write"],
            },
            {
                "name": "restore",
                "credentials": [{"accessKey": READER.key_id, "secretKey": READER.secret}],
                "actions": ["Read", "List"],
            },
        ]
    }
    # A configuração vai pelo comando, sem montar arquivo do host, que no macOS depende das
    # pastas compartilhadas do Docker Desktop.
    command = (
        f"printf '%s' '{json.dumps(identities)}' > /tmp/s3.json && "
        f"exec weed server -s3 -s3.port={S3_PORT} -s3.config=/tmp/s3.json -dir=/data"
    )
    container = (
        DockerContainer(SEAWEEDFS_IMAGE)
        .with_exposed_ports(S3_PORT)
        .with_kwargs(entrypoint=["sh", "-c"])
        .with_command([command])
    )
    with container:
        host = container.get_container_host_ip()
        endpoint = f"http://{host}:{container.get_exposed_port(S3_PORT)}"
        storage = S3(endpoint)
        _create_buckets_when_ready(storage)
        yield storage


def _create_buckets_when_ready(storage: S3) -> None:
    """O bucket dos testes imita o da produção: Object Lock em compliance por 30 dias."""
    deadline = time.monotonic() + S3_READY_TIMEOUT_S
    lock = "x-amz-bucket-object-lock-enabled: true"
    while True:
        try:
            created = storage.status(
                ADMIN, "-X", "PUT", "-H", lock, "-o", "/dev/null", storage.url()
            )
            if created == HTTP_OK:
                break
        except subprocess.CalledProcessError:
            pass  # O gateway ainda não aceita conexões.
        if time.monotonic() > deadline:
            raise TimeoutError("o S3 do SeaweedFS não ficou pronto")
        time.sleep(S3_READY_POLL_S)
    # O S3 exige Content-MD5 na configuração do Object Lock; é checagem de integridade.
    digest = hashlib.md5(OBJECT_LOCK_30_DAYS.encode(), usedforsecurity=False).digest()
    md5 = base64.b64encode(digest).decode()
    assert (
        storage.status(
            ADMIN,
            "-X",
            "PUT",
            "-H",
            f"Content-MD5: {md5}",
            "--data-binary",
            OBJECT_LOCK_30_DAYS,
            "-o",
            "/dev/null",
            f"{storage.url()}?object-lock",
        )
        == HTTP_OK
    )
    unlocked = f"{storage.endpoint}/{UNLOCKED_BUCKET}/"
    assert storage.status(ADMIN, "-X", "PUT", "-o", "/dev/null", unlocked) == HTTP_OK


@dataclass
class FakeHealthchecks:
    url: str
    pings: list[str] = field(default_factory=list)


@pytest.fixture
def healthchecks() -> Iterator[FakeHealthchecks]:
    check_path = f"/ping/{uuid.uuid4()}"
    pings: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self._record()

        def do_POST(self) -> None:
            self._record()

        def _record(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(length)
            pings.append(self.path.removeprefix(check_path) or "/")
            self.send_response(HTTP_OK)
            self.end_headers()
            self.wfile.write(b"OK")

        def log_message(self, *_args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield FakeHealthchecks(f"http://127.0.0.1:{server.server_port}{check_path}", pings)
    finally:
        server.shutdown()
        server.server_close()


@dataclass(frozen=True)
class Database:
    container_id: str
    user: str
    name: str

    def scalar(self, sql: str) -> str:
        out = subprocess.run(
            [
                "docker",
                "exec",
                self.container_id,
                "psql",
                "--no-psqlrc",
                "--tuples-only",
                "--no-align",
                "--set",
                "ON_ERROR_STOP=1",
                f"--username={self.user}",
                f"--dbname={self.name}",
                f"--command={sql}",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        return out.stdout.strip()


def _new_database(postgres: PostgresContainer, prefix: str) -> Database:
    name = f"{prefix}_{uuid.uuid4().hex[:12]}"
    admin_url = postgres.get_connection_url().replace("postgresql://", "postgresql+psycopg://")
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    engine.dispose()
    return Database(postgres.get_wrapped_container().id, postgres.username, name)


@dataclass(frozen=True)
class SourceDatabase:
    database: Database
    # Gravado no banco para provar que o objeto enviado está cifrado.
    canary: str


@pytest.fixture
def source_db(postgres: PostgresContainer, db_settings: Settings) -> SourceDatabase:
    """Banco no head do Alembic com uma conta e uma execução, como o de produção."""
    database = _new_database(postgres, "backup_src")
    settings = db_settings.model_copy(update={"db_name": database.name})
    run_migrations(settings, "head")
    canary = f"CANARY-{uuid.uuid4()}"
    engine = make_engine(settings)
    factory = make_session_factory(engine)
    with unit_of_work(factory) as session:
        owner_id = create_admin_user(session, canary)
    insert_finished_run(factory, owner_id, created_at=datetime.now(UTC))
    engine.dispose()
    return SourceDatabase(database, canary)


@pytest.fixture
def restore_db(postgres: PostgresContainer) -> Database:
    return _new_database(postgres, "restore_dst")


@dataclass(frozen=True)
class AgeKeys:
    identity: Path
    recipients: Path


@pytest.fixture
def age_keys(tmp_path: Path) -> AgeKeys:
    identity = tmp_path / "identity.txt"
    subprocess.run(["age-keygen", "-o", str(identity)], check=True, capture_output=True)
    public = subprocess.run(
        ["age-keygen", "-y", str(identity)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    recipients = tmp_path / "age-recipients.txt"
    recipients.write_text(f"# chave de teste\n{public}")
    return AgeKeys(identity, recipients)


@pytest.fixture
def known_revisions() -> str:
    _, known = read_code_revisions(str(ROOT / "alembic.ini"))
    return " ".join(sorted(known))
