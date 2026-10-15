"""scripts/stress-runs.sh: N suítes `tests/runs` em paralelo (plano da fatia 8, 5.3-4)."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "stress-runs.sh"
SCRIPT_TIMEOUT_S = 30
# Um `uv` falso: registra cada chamada e devolve o código pedido para a rodada.
FAKE_UV = """#!/bin/sh
echo "uv $*" >>"$FAKE_CALLS"
exit "${FAKE_UV_RC:-0}"
"""


def stress(
    tmp_path: Path, *args: str, rc: int = 0
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    uv = bin_dir / "uv"
    uv.write_text(FAKE_UV)
    uv.chmod(0o755)
    env = {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "FAKE_CALLS": str(tmp_path / "calls"),
        "FAKE_UV_RC": str(rc),
    }
    result = subprocess.run(  # noqa: S603 - script do repositório
        ["bash", str(SCRIPT), *args],  # noqa: S607 - o bash do PATH, como no terminal
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=SCRIPT_TIMEOUT_S,
        cwd=ROOT,
    )
    calls = tmp_path / "calls"
    return result, calls.read_text().splitlines() if calls.exists() else []


def test_roda_n_suites_em_paralelo_sem_cache_do_pytest(tmp_path: Path) -> None:
    result, calls = stress(tmp_path, "3")

    assert result.returncode == 0, result.stderr
    assert calls == ["uv run pytest tests/runs -q -p no:cacheprovider"] * 3
    assert "3 de 3 suítes verdes" in result.stdout


def test_uma_suite_vermelha_reprova_e_aponta_o_log(tmp_path: Path) -> None:
    result, _ = stress(tmp_path, "2", rc=1)

    assert result.returncode == 1
    assert "0 de 2 suítes verdes" in result.stdout
    assert "rodada 1" in result.stdout


@pytest.mark.parametrize("rounds", ["0", "-1", "dois", ""])
def test_numero_de_suites_invalido_nao_roda_nada(tmp_path: Path, rounds: str) -> None:
    result, calls = stress(tmp_path, rounds)

    assert result.returncode == 2
    assert calls == []


def test_passa_no_shellcheck() -> None:
    shellcheck = shutil.which("shellcheck")
    if shellcheck is None:
        pytest.skip("shellcheck ausente")
    result = subprocess.run(  # noqa: S603 - binário localizado pelo which
        [shellcheck, str(SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
        timeout=SCRIPT_TIMEOUT_S,
    )
    assert result.returncode == 0, result.stdout
