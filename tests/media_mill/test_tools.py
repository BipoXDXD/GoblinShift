"""Execução do FFmpeg: ambiente vazio, saídas em arquivo, tempo e orçamento (ADR 036)."""

import sys
from pathlib import Path

import pytest

from goblinshift.processing.media_mill.tools import TimeBudget, ToolTimeoutError, run_tool
from goblinshift.processing.recipe import FailureCode, RecipeFailure


def python(code: str) -> list[str]:
    return [sys.executable, "-I", "-c", code]


def test_filho_roda_sem_herdar_o_ambiente(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FFREPORT", "file=/tmp/vazou.log")

    outcome = run_tool(
        python("import os; print('FFREPORT' in os.environ, 'PATH' in os.environ)"),
        timeout_s=30,
        scratch=tmp_path,
        name="env",
    )

    assert outcome.succeeded
    assert outcome.stdout.read_text().strip() == "False False"


def test_stdout_e_stderr_vao_para_arquivos_do_rascunho(tmp_path: Path) -> None:
    outcome = run_tool(
        python("import sys; print('saida'); print('log', file=sys.stderr); sys.exit(3)"),
        timeout_s=30,
        scratch=tmp_path,
        name="io",
    )

    assert (outcome.returncode, outcome.succeeded) == (3, False)
    assert (outcome.stdout, outcome.log) == (tmp_path / "io.out", tmp_path / "io.log")
    assert outcome.stdout.read_text() == "saida\n"
    assert outcome.log.read_text() == "log\n"


def test_filho_nao_le_o_stdin(tmp_path: Path) -> None:
    outcome = run_tool(
        python("import sys; print(repr(sys.stdin.read()))"),
        timeout_s=30,
        scratch=tmp_path,
        name="in",
    )

    assert outcome.stdout.read_text().strip() == "''"


def test_arquivo_maior_que_o_teto_mata_o_filho(tmp_path: Path) -> None:
    target = tmp_path / "grande.bin"
    code = f"open({str(target)!r}, 'wb').write(b'x' * (300 * 1024 * 1024))"

    outcome = run_tool(python(code), timeout_s=60, scratch=tmp_path, name="fsize")

    assert not outcome.succeeded
    assert target.stat().st_size <= 256 * 1024 * 1024


def test_chamada_que_passa_do_prazo_e_interrompida(tmp_path: Path) -> None:
    with pytest.raises(ToolTimeoutError):
        run_tool(
            python("import time; time.sleep(30)"), timeout_s=0.5, scratch=tmp_path, name="slow"
        )


def test_orcamento_limita_o_prazo_ao_que_sobra() -> None:
    now = [100.0]
    budget = TimeBudget(60, clock=lambda: now[0])
    now[0] = 140.0

    assert budget.timeout(30) == 20.0
    assert budget.timeout(10) == 10.0


def test_orcamento_esgotado_e_falha_de_recurso() -> None:
    now = [0.0]
    budget = TimeBudget(60, clock=lambda: now[0])
    now[0] = 60.0

    with pytest.raises(RecipeFailure) as failure:
        budget.timeout(30)

    assert failure.value.code is FailureCode.RESOURCE_LIMIT_EXCEEDED
