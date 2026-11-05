"""Biblioteca dos scripts de ponta a ponta com a chave real: scripts/e2e-lib.sh.

No job agendado a chave é de produção: ela chega ao curl sem passar pela linha de comando, e nem
o gerador de fixtures, que importa as dependências do projeto, nem o log a enxergam.
"""

import json
import os
import stat
import subprocess
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import ClassVar

import pytest

ROOT = Path(__file__).resolve().parents[1]
LIBRARY = ROOT / "scripts" / "e2e-lib.sh"
QUOTA_SCRIPT = ROOT / "scripts" / "e2e-quota-check.sh"
CANARY = "gsk_CANARY-e2e-lib"
OK = 200
UNAUTHORIZED = 401
ACCOUNT = {
    "scopes": ["files:read", "files:write", "runs:read", "runs:write"],
    "limits": {
        "runs_per_24h": 30,
        "input_bytes_per_24h": 2147483648,
        "storage_bytes": 4294967296,
    },
    "usage": {
        "runs_last_24h": 0,
        "input_bytes_last_24h": 0,
        "storage_reserved_bytes": 0,
        "active_run_id": None,
    },
    "terms": {"acceptance_required": False},
}


class FakeApi(BaseHTTPRequestHandler):
    status: ClassVar[int] = OK
    seen: ClassVar[list[tuple[str, str | None]]] = []

    def do_GET(self) -> None:
        FakeApi.seen.append((self.path, self.headers.get("Authorization")))
        body = json.dumps(ACCOUNT if FakeApi.status == OK else {"code": "UNAUTHORIZED"}).encode()
        self.send_response(FakeApi.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


@pytest.fixture
def api_base() -> Iterator[str]:
    FakeApi.status = OK
    FakeApi.seen = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeApi)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()
    thread.join()


def bash(script: str, base: str, path: str | None = None) -> subprocess.CompletedProcess[str]:
    command = f'set -euo pipefail; source "$1"; e2e_setup "$2"; {script}'
    return subprocess.run(  # noqa: S603 - script do repositório
        ["/bin/bash", "-c", command, "_", str(LIBRARY), base],
        capture_output=True,
        text=True,
        check=False,
        env={"GS_API_KEY": CANARY, "PATH": path or os.environ["PATH"]},
    )


def stub(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def test_api_autentica_com_a_chave(api_base: str) -> None:
    result = bash("api GET /me", api_base)

    assert result.returncode == 0, result.stderr
    assert FakeApi.seen == [("/api/v1/me", f"Bearer {CANARY}")]


def test_a_chave_nao_vai_para_a_linha_de_comando_do_curl(api_base: str, tmp_path: Path) -> None:
    arguments = tmp_path / "arguments"
    stub(tmp_path, "curl", f'echo "$@" >"{arguments}"')

    result = bash("api GET /me", api_base, f"{tmp_path}:/usr/bin:/bin")

    assert result.returncode == 0, result.stderr
    assert arguments.read_text()
    assert CANARY not in arguments.read_text()


def test_o_gerador_de_fixtures_nao_herda_a_chave(api_base: str, tmp_path: Path) -> None:
    seen = tmp_path / "seen"
    stub(tmp_path, "uv", f'echo "chave=${{GS_API_KEY:-ausente}}" >"{seen}"')

    command = f'generate_fixtures pdf-press "{tmp_path}/fx"'

    result = bash(command, api_base, f"{tmp_path}:/usr/bin:/bin")

    assert result.returncode == 0, result.stderr
    assert seen.read_text() == "chave=ausente\n"


def quota_check(base: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - script do repositório
        [str(QUOTA_SCRIPT), base],
        capture_output=True,
        text=True,
        check=False,
        env={"GS_API_KEY": CANARY, "PATH": os.environ["PATH"]},
    )


def test_conferencia_da_cota_le_a_conta_e_devolve_a_saida_do_passo(api_base: str) -> None:
    result = quota_check(api_base)

    assert result.returncode == 0, result.stderr
    assert result.stdout == "fits=true\n"
    assert FakeApi.seen == [("/api/v1/me", f"Bearer {CANARY}")]


def test_chave_recusada_falha_sem_saida_e_sem_vazar_a_chave(api_base: str) -> None:
    FakeApi.status = UNAUTHORIZED

    result = quota_check(api_base)

    assert result.returncode != 0
    assert result.stdout == ""
    assert CANARY not in result.stderr
