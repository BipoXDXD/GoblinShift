"""A CLI processa a suíte de fixtures (docs/GoblinShiftPlan.md, fatia 2 em 3.9).

Cada pasta em `fixtures/` tem `input.csv`, `params.json`, `expected.json` (o que o relatório deve
dizer) e, se a tabela for aceita, `expected.csv` com os bytes exatos da saída.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from goblinshift.entrypoints.csv_janitor_cli import (
    EXIT_ALL_CLEANED,
    EXIT_SOME_REJECTED,
    EXIT_USAGE,
    main,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _cases(outcome: str) -> list[Path]:
    return sorted(
        case
        for case in FIXTURES.iterdir()
        if json.loads((case / "expected.json").read_text())["outcome"] == outcome
    )


CLEANED = _cases("cleaned")
REJECTED = _cases("rejected")


def run(capsys: pytest.CaptureFixture[str], *args: str | Path) -> tuple[int, dict[str, Any]]:
    exit_code = main([str(arg) for arg in args])
    stdout = capsys.readouterr().out
    return exit_code, json.loads(stdout) if stdout else {}


def run_fixture(
    case: Path, output_dir: Path, capsys: pytest.CaptureFixture[str]
) -> tuple[int, dict[str, Any], dict[str, Any]]:
    exit_code, report = run(
        capsys, "--params", case / "params.json", "--output-dir", output_dir, case / "input.csv"
    )
    [table] = report["tables"]
    return exit_code, table, json.loads((case / "expected.json").read_text())


@pytest.mark.parametrize("case", CLEANED, ids=[case.name for case in CLEANED])
def test_cli_limpa_a_fixture_aceita(
    case: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code, table, expected = run_fixture(case, tmp_path, capsys)

    assert exit_code == EXIT_ALL_CLEANED
    assert table["outcome"] == "cleaned"
    assert table["counts"] == expected["counts"]
    assert table["formula_cells"] == expected["formula_cells"]
    assert [w["code"] for w in table["warnings"]] == expected["warnings"]
    assert (tmp_path / "input.csv").read_bytes() == (case / "expected.csv").read_bytes()


@pytest.mark.parametrize("case", REJECTED, ids=[case.name for case in REJECTED])
def test_cli_rejeita_a_fixture_invalida_sem_gravar_saida(
    case: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code, table, expected = run_fixture(case, tmp_path, capsys)

    assert exit_code == EXIT_SOME_REJECTED
    assert (table["outcome"], table["code"]) == ("rejected", expected["code"])
    assert list(tmp_path.iterdir()) == []


def test_suite_tem_casos_aceitos_e_rejeitados() -> None:
    assert len(CLEANED) >= 5
    assert len(REJECTED) >= 3


def test_lote_misto_relata_na_ordem_das_entradas(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    good = tmp_path / "boa.csv"
    good.write_bytes(b"a\r\n1\r\n")
    bad = tmp_path / "ruim.csv"
    bad.write_bytes(b"a,b\r\n1\r\n")
    params = tmp_path / "params.json"
    params.write_text("{}")

    exit_code, report = run(capsys, "--params", params, "--output-dir", tmp_path / "out", bad, good)

    assert exit_code == EXIT_SOME_REJECTED
    assert [t["outcome"] for t in report["tables"]] == ["rejected", "cleaned"]
    assert [p.name for p in (tmp_path / "out").iterdir()] == ["boa.csv"]


@pytest.mark.parametrize(
    "config",
    ['{"filters": "x"}', '{"trim_columns": [], "trim_columns": ["a"]}', '{"a": NaN}', "[]", "{"],
)
def test_configuracao_invalida_sai_com_erro_de_uso(
    config: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    params = tmp_path / "params.json"
    params.write_text(config)
    table = tmp_path / "t.csv"
    table.write_bytes(b"a\r\n1\r\n")

    exit_code, _ = run(capsys, "--params", params, "--output-dir", tmp_path / "out", table)

    assert exit_code == EXIT_USAGE
    assert not (tmp_path / "out").exists()


def test_entradas_com_o_mesmo_nome_de_saida_sao_recusadas(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    params = tmp_path / "params.json"
    params.write_text("{}")
    (tmp_path / "x").mkdir()
    (tmp_path / "y").mkdir()

    exit_code, _ = run(
        capsys,
        "--params",
        params,
        "--output-dir",
        tmp_path,
        tmp_path / "x" / "t.csv",
        tmp_path / "y" / "t.csv",
    )

    assert exit_code == EXIT_USAGE


def test_nao_sobrescreve_saida_existente(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    params = tmp_path / "params.json"
    params.write_text("{}")
    table = tmp_path / "t.csv"
    table.write_bytes(b"a\r\n1\r\n")
    out = tmp_path / "out"
    out.mkdir()
    (out / "t.csv").write_bytes(b"anterior")

    exit_code, _ = run(capsys, "--params", params, "--output-dir", out, table)

    assert exit_code == EXIT_USAGE
    assert (out / "t.csv").read_bytes() == b"anterior"


def test_entrada_ilegivel_sai_com_erro_de_uso(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    params = tmp_path / "params.json"
    params.write_text("{}")

    exit_code, _ = run(capsys, "--params", params, "--output-dir", tmp_path, tmp_path / "nao.csv")

    assert exit_code == EXIT_USAGE
