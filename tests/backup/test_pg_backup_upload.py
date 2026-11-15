"""O marcador só avança com a resposta de sucesso do PutObject."""

import os
import re
import stat
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

pytestmark = pytest.mark.host_python

SCRIPT = Path(__file__).resolve().parents[2] / "deploy" / "backup" / "pg-backup.sh"
PREVIOUS_SUCCESS = "2026-11-01T03:05:42Z\n"
FAKE_DOCKER = """#!/bin/sh
case "$*" in
  *psql*)
    if [ "$FAKE_BACKUP_FAILURE" = psql ]; then
      IFS= read -r begin
      IFS= read -r query
      exec 0<&-
      echo t
      exit 1
    fi
    trap 'rm -f -- "${STATE_DIRECTORY:?}/snapshot-active"' EXIT
    while IFS= read -r sql; do
      case "$sql" in
        *alembic_version*) echo t ;;
        *"count(*) FROM public.app_user"*) echo 1 ;;
        *pg_export_snapshot*)
          touch "$STATE_DIRECTORY/snapshot-active"
          echo 00000003-00000010-1
          ;;
      esac
    done
    ;;
  *pg_dump*--snapshot=00000003-00000010-1*)
    test -f "$STATE_DIRECTORY/snapshot-active" || exit 2
    echo dump
    if [ "$FAKE_BACKUP_FAILURE" = pg_dump ]; then exit 1; fi
    ;;
  *) exit 2 ;;
esac
"""
FAKE_AGE = """#!/bin/sh
while [ "$#" -gt 0 ]; do
  if [ "$1" = --output ]; then cat >"$2"; exit $?; fi
  shift
done
exit 2
"""


@pytest.mark.parametrize(
    ("status", "failure"),
    [
        pytest.param(200, "", id="200"),
        pytest.param(307, "", id="307"),
        pytest.param(403, "", id="403"),
        pytest.param(200, "pg_dump", id="dump-parcial"),
        pytest.param(200, "psql", id="psql-fecha-stdin"),
        pytest.param(200, "marker", id="marcador-e-diretorio"),
    ],
)
def test_o_put_precisa_confirmar_o_upload_para_avancar_o_marcador(
    tmp_path: Path, status: int, failure: str
) -> None:
    # Exercita o curl real com HTTP local. O teste de integração usa PostgreSQL e age reais.
    events: list[tuple[str, str]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_PUT(self) -> None:
            self.rfile.read(int(self.headers["Content-Length"]))
            events.append(("PUT", self.path))
            self.send_response(status)
            if status == 307:
                self.send_header("Location", "/para-outro-bucket")
            self.end_headers()

        def do_GET(self) -> None:
            events.append(("GET", self.path))
            self.send_response(200)
            self.end_headers()

    tools = tmp_path / "bin"
    tools.mkdir()
    for name, source in (("docker", FAKE_DOCKER), ("age", FAKE_AGE)):
        path = tools / name
        path.write_text(source)
        path.chmod(0o700)
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    marker = state / "last-upload-ok"
    if failure == "marker":
        marker.mkdir()
    else:
        marker.write_text(PREVIOUS_SUCCESS)
    recipients = tmp_path / "recipients"
    recipients.write_text("destinatario-de-teste\n")

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        url = f"http://127.0.0.1:{server.server_port}"
        env = {
            "PATH": f"{tools}{os.pathsep}{os.environ['PATH']}",
            "BACKUP_PG_CONTAINER": "postgres-de-teste",
            "BACKUP_AGE_RECIPIENTS_FILE": str(recipients),
            "BACKUP_S3_ENDPOINT": url,
            "BACKUP_S3_REGION": "us-east-1",
            "BACKUP_S3_BUCKET": "test-bucket",
            "BACKUP_S3_KEY_ID": "test-writer",
            "BACKUP_S3_SECRET": "test-credential",
            "BACKUP_HEALTHCHECK_URL": f"{url}/check",
            "STATE_DIRECTORY": str(state),
            "FAKE_BACKUP_FAILURE": failure,
        }
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = subprocess.run(
                ["bash", str(SCRIPT)],
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        finally:
            server.shutdown()
            thread.join()

    assert sorted(state.iterdir()) == [marker]
    assert events[0] == ("GET", "/check/start")
    if failure in {"psql", "pg_dump"}:
        assert result.returncode != 0
        assert events == [("GET", "/check/start"), ("GET", "/check/fail")]
        assert marker.read_text() == PREVIOUS_SUCCESS
        return

    assert len(events) == 3
    assert events[1][0] == "PUT"
    assert events[1][1].startswith("/test-bucket/pg/")
    if failure == "marker":
        assert result.returncode != 0
        assert events[2] == ("GET", "/check/fail")
        assert list(marker.iterdir()) == []
    elif status == 200:
        assert result.returncode == 0, result.stderr
        assert events[2] == ("GET", "/check")
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\n", marker.read_text())
        assert marker.read_text() != PREVIOUS_SUCCESS
        assert stat.S_IMODE(marker.stat().st_mode) == 0o600
    else:
        assert result.returncode != 0
        assert events[2] == ("GET", "/check/fail")
        assert marker.read_text() == PREVIOUS_SUCCESS
