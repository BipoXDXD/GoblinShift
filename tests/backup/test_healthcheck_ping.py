"""Ping ao healthchecks.io: a URL do check é segredo e nunca vai para a linha de comando.

Quem tem a URL pinga sucesso no lugar do backup e cala o alerta. No argv do curl ela aparece no
`ps` de qualquer usuário do host; pelo stdin (`--config -`), não. O curl aqui é falso e só
registra o argv e o stdin.
"""

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
LIB = ROOT / "deploy" / "backup" / "lib.sh"
CHECK_URL = "https://hc-ping.com/0b1c2d3e-CANARY-check"

FAKE_CURL = """#!/bin/sh
printf '%s\\n' "$@" >"$FAKE_CURL_DIR/argv"
cat >"$FAKE_CURL_DIR/stdin"
exit "${FAKE_CURL_RC:-0}"
"""


@pytest.fixture
def fake_curl(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    curl = bin_dir / "curl"
    curl.write_text(FAKE_CURL)
    curl.chmod(0o755)
    return tmp_path


def ping(fake_dir: Path, url: str, suffix: str, rc: int = 0) -> subprocess.CompletedProcess[str]:
    env = {
        "PATH": f"{fake_dir / 'bin'}:{os.environ['PATH']}",
        "FAKE_CURL_DIR": str(fake_dir),
        "FAKE_CURL_RC": str(rc),
    }
    script = 'set -euo pipefail; source "$1"; healthcheck_ping "$2" "$3"'
    return subprocess.run(
        ["bash", "-c", script, "_", str(LIB), url, suffix],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("suffix", ["/start", "", "/fail"])
def test_url_do_check_vai_pelo_stdin_e_nunca_pelo_argv(fake_curl: Path, suffix: str) -> None:
    result = ping(fake_curl, CHECK_URL, suffix)

    assert result.returncode == 0, result.stderr
    assert "CANARY" not in (fake_curl / "argv").read_text()
    assert (fake_curl / "stdin").read_text() == f'url = "{CHECK_URL}{suffix}"\n'


def test_barra_final_da_url_nao_duplica(fake_curl: Path) -> None:
    ping(fake_curl, CHECK_URL + "/", "/fail")

    assert (fake_curl / "stdin").read_text() == f'url = "{CHECK_URL}/fail"\n'


def test_aspas_e_barra_invertida_na_url_sao_escapadas(fake_curl: Path) -> None:
    ping(fake_curl, 'https://h/a"b\\c', "")

    assert (fake_curl / "stdin").read_text() == 'url = "https://h/a\\"b\\\\c"\n'


def test_ping_que_falha_so_avisa(fake_curl: Path) -> None:
    result = ping(fake_curl, CHECK_URL, "/start", rc=7)

    assert result.returncode == 0
    assert "falhou" in result.stderr
    assert "CANARY" not in result.stderr


def test_sem_url_nao_chama_o_curl(fake_curl: Path) -> None:
    result = ping(fake_curl, "", "/start")

    assert result.returncode == 0
    assert not (fake_curl / "argv").exists()
