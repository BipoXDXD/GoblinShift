"""Executor local: contrato do diretório de trabalho e códigos de saída do processo.

Os casos centrais rodam o executor num subprocesso real, como o worker fará; variações de job
malformado chamam `main` no próprio processo, que é o mesmo código sem o custo de um interpretador
novo por caso.
"""

import csv
import hashlib
import json
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from goblinshift.processing.csv_janitor import Limits
from goblinshift.processing.executor import ExitCode, main

VALID = b"nome,cidade\r\nAna,Recife\r\n"
INVALID = b'nome,cidade\r\n"Ana,Recife\r\n'
SUBPROCESS_TIMEOUT_SECONDS = 60


def make_work_dir(
    root: Path,
    tables: list[tuple[str, bytes]],
    parameters: dict[str, Any] | None = None,
) -> Path:
    work = root / "work"
    inputs = work / "inputs"
    inputs.mkdir(parents=True)
    entries = []
    for position, (original_name, data) in enumerate(tables):
        name = f"{position:04}.bin"
        (inputs / name).write_bytes(data)
        entries.append(
            {
                "position": position,
                "path": name,
                "original_name": original_name,
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    write_job(
        work,
        {
            "recipe_id": "csv-janitor",
            "recipe_version": 1,
            "parameters": parameters or {},
            "inputs": entries,
        },
    )
    return work


def write_job(work: Path, job: dict[str, Any]) -> None:
    (work / "job.json").write_text(json.dumps(job), encoding="utf-8")


def read_job(work: Path) -> dict[str, Any]:
    job: dict[str, Any] = json.loads((work / "job.json").read_text(encoding="utf-8"))
    return job


def run_executor(work: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - argumentos montados pelo teste, sem shell
        [sys.executable, "-m", "goblinshift.processing.executor", str(work)],
        capture_output=True,
        text=True,
        timeout=SUBPROCESS_TIMEOUT_SECONDS,
        check=False,
    )


def progress_positions(work: Path) -> list[int]:
    lines = (work / "progress.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line)["position"] for line in lines]


def test_sucesso_com_rejeicao_grava_saidas_relatorio_e_progresso(tmp_path: Path) -> None:
    work = make_work_dir(tmp_path, [("clientes.csv", VALID), ("ruim.csv", INVALID)])

    result = run_executor(work)

    assert result.returncode == ExitCode.SUCCEEDED, result.stderr
    assert sorted(p.name for p in (work / "out").iterdir()) == ["clientes.csv", "report.json"]
    assert (work / "out" / "clientes.csv").read_bytes() == VALID
    report = json.loads((work / "out" / "report.json").read_text(encoding="utf-8"))
    assert report == {
        "schema_version": 1,
        "recipe_id": "csv-janitor",
        "recipe_version": 1,
        "summary": {"inputs": 2, "ok": 1, "rejected": 1, "outputs": 1},
        "items": [
            {
                "position": 0,
                "original_name": "clientes.csv",
                "status": "ok",
                "outputs": ["clientes.csv"],
                "counts": {
                    "records_read": 1,
                    "filtered_out": 0,
                    "duplicates_removed": 0,
                    "written": 1,
                    "blank_lines_ignored": 0,
                },
                "warnings": [],
            },
            {
                "position": 1,
                "original_name": "ruim.csv",
                "status": "rejected",
                "code": "CSV_STRUCTURE_INVALID",
                "reason": report["items"][1]["reason"],
            },
        ],
        "warnings": [],
    }
    assert report["items"][1]["reason"]
    assert progress_positions(work) == [0, 1]
    assert not (work / "failure.json").exists()


def test_todas_rejeitadas_terminam_com_sucesso_do_processo_e_relatorio_sem_saidas(
    tmp_path: Path,
) -> None:
    work = make_work_dir(tmp_path, [("a.csv", INVALID), ("b.csv", INVALID)])

    result = run_executor(work)

    assert result.returncode == ExitCode.SUCCEEDED, result.stderr
    report = json.loads((work / "out" / "report.json").read_text(encoding="utf-8"))
    assert report["summary"] == {"inputs": 2, "ok": 0, "rejected": 2, "outputs": 0}
    assert [p.name for p in (work / "out").iterdir()] == ["report.json"]


def test_entrada_com_sha256_diferente_e_falha_de_integridade(tmp_path: Path) -> None:
    work = make_work_dir(tmp_path, [("a.csv", VALID)])
    (work / "inputs" / "0000.bin").write_bytes(VALID.replace(b"Ana", b"Eva"))

    result = run_executor(work)

    assert result.returncode == ExitCode.INTEGRITY
    assert not (work / "out" / "report.json").exists()
    assert not (work / "failure.json").exists()


def test_job_malformado_e_falha_de_infraestrutura(tmp_path: Path) -> None:
    work = make_work_dir(tmp_path, [("a.csv", VALID)])
    (work / "job.json").write_text("{não é json", encoding="utf-8")

    result = run_executor(work)

    assert result.returncode == ExitCode.INFRASTRUCTURE
    assert not (work / "out").exists()


def test_falha_deterministica_grava_o_codigo_em_failure_json(tmp_path: Path) -> None:
    work = make_work_dir(tmp_path, [(f"t{i}.csv", VALID) for i in range(6)])

    result = run_executor(work)

    assert result.returncode == ExitCode.RECIPE_FAILURE
    failure = json.loads((work / "failure.json").read_text(encoding="utf-8"))
    assert failure["code"] == "INPUT_LIMIT_EXCEEDED"
    assert failure["detail"]
    assert not (work / "out" / "report.json").exists()


def test_sem_diretorio_de_trabalho_no_argumento_e_falha_de_infraestrutura() -> None:
    assert main([]) == ExitCode.INFRASTRUCTURE


def test_tamanho_diferente_do_declarado_e_falha_de_integridade(tmp_path: Path) -> None:
    work = make_work_dir(tmp_path, [("a.csv", VALID)])
    job = read_job(work)
    job["inputs"][0]["size"] += 1
    write_job(work, job)

    assert main([str(work)]) == ExitCode.INTEGRITY


def test_entrada_ausente_e_falha_de_integridade(tmp_path: Path) -> None:
    work = make_work_dir(tmp_path, [("a.csv", VALID)])
    (work / "inputs" / "0000.bin").unlink()

    assert main([str(work)]) == ExitCode.INTEGRITY


def test_entrada_que_e_symlink_e_falha_de_integridade(tmp_path: Path) -> None:
    work = make_work_dir(tmp_path, [("a.csv", VALID)])
    real = tmp_path / "fora.csv"
    real.write_bytes(VALID)
    (work / "inputs" / "0000.bin").unlink()
    (work / "inputs" / "0000.bin").symlink_to(real)

    assert main([str(work)]) == ExitCode.INTEGRITY


type Mutation = Callable[[dict[str, Any]], object]


def _mutations() -> Iterator[tuple[str, Mutation]]:
    yield "campo desconhecido", lambda job: job.update(extra=1)
    yield "campo ausente", lambda job: job.pop("parameters")
    yield "receita inexistente", lambda job: job.update(recipe_id="nada")
    yield "versão inexistente", lambda job: job.update(recipe_version=99)
    yield "versão booleana", lambda job: job.update(recipe_version=True)
    yield "parâmetros não são objeto", lambda job: job.update(parameters=[])
    yield "parâmetro inválido", lambda job: job.update(parameters={"formula_guard": "x"})
    yield "traversal no caminho", lambda job: job["inputs"][0].update(path="../job.json")
    yield "caminho com separador", lambda job: job["inputs"][0].update(path="a/0000.bin")
    yield "posição fora de ordem", lambda job: job["inputs"][0].update(position=1)
    yield "sha256 fora do formato", lambda job: job["inputs"][0].update(sha256="ABC")
    yield "tamanho negativo", lambda job: job["inputs"][0].update(size=-1)
    yield "entrada com campo extra", lambda job: job["inputs"][0].update(owner="x")
    yield "nome original não é texto", lambda job: job["inputs"][0].update(original_name=1)


@pytest.mark.parametrize(("case", "mutate"), list(_mutations()), ids=[c for c, _ in _mutations()])
def test_job_fora_do_contrato_e_falha_de_infraestrutura(
    tmp_path: Path, case: str, mutate: Mutation
) -> None:
    work = make_work_dir(tmp_path, [("a.csv", VALID)])
    job = read_job(work)
    mutate(job)
    write_job(work, job)

    assert main([str(work)]) == ExitCode.INFRASTRUCTURE, case
    assert not (work / "out" / "report.json").exists()


def test_job_com_chave_repetida_e_falha_de_infraestrutura(tmp_path: Path) -> None:
    work = make_work_dir(tmp_path, [("a.csv", VALID)])
    text = (work / "job.json").read_text(encoding="utf-8")
    (work / "job.json").write_text(text[:-1] + ', "recipe_version": 1}', encoding="utf-8")

    assert main([str(work)]) == ExitCode.INFRASTRUCTURE


def test_saida_ja_ocupada_e_falha_de_infraestrutura(tmp_path: Path) -> None:
    work = make_work_dir(tmp_path, [("a.csv", VALID)])
    (work / "out").mkdir()
    (work / "out" / "resto.csv").write_bytes(b"de outra tentativa")

    assert main([str(work)]) == ExitCode.INFRASTRUCTURE


def test_ajusta_o_field_size_limit_do_csv_no_inicio(tmp_path: Path) -> None:
    work = make_work_dir(tmp_path, [("a.csv", VALID)])
    # Acima do piso que a biblioteca impõe sozinha, para só o executor poder baixá-lo.
    previous = csv.field_size_limit(2 * Limits().record_bytes)
    try:
        assert main([str(work)]) == ExitCode.SUCCEEDED
        assert csv.field_size_limit() == Limits().record_bytes
    finally:
        csv.field_size_limit(previous)
