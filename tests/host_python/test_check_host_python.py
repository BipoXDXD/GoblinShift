"""O checador do Python do host recusa API acima do 3.12 nos pacotes de deploy/.

Roda na suíte principal e no job "Scripts do host (Python 3.12)" do CI: o vermin lê o código
sem executá-lo, então o resultado não depende do interpretador que roda o teste.
"""

from pathlib import Path

import pytest

from scripts import check_host_python

pytestmark = pytest.mark.host_python

HOST_PACKAGES = {
    "acceptance",
    "backup",
    "executor_ctl",
    "executor_inventory",
    "openobserve",
    "tls_check",
}


def _package(tmp_path: Path, source: str) -> Path:
    # Nome único por teste: o pacote importado fica em sys.modules e não pode vazar para outro.
    package = tmp_path / f"pacote_{tmp_path.name}"
    package.mkdir()
    (package / "modulo.py").write_text(source)
    return package


def test_api_que_so_existe_no_313_reprova_o_piso(tmp_path: Path) -> None:
    package = _package(tmp_path, "import copy\n\nREPLACE = copy.replace\n")

    report = check_host_python.version_floor_report([package])

    assert "copy.replace" in report
    assert "3.13" in report


def test_comentario_novm_nao_cala_o_piso(tmp_path: Path) -> None:
    # O vermin aceita `# novm` para ignorar uma linha; no host, a linha quebra do mesmo jeito.
    package = _package(
        tmp_path, "import copy\n\n\ndef clone(x):\n    return copy.replace(x)  # novm\n"
    )

    report = check_host_python.version_floor_report([package])

    assert "copy.replace" in report


def test_api_que_existe_no_312_passa_no_piso(tmp_path: Path) -> None:
    package = _package(tmp_path, "import tomllib\n\nLOADS = tomllib.loads\n")

    assert check_host_python.version_floor_report([package]) == ""


def test_as_pastas_do_host_sao_as_py312_do_pyproject() -> None:
    paths = check_host_python.host_paths()

    assert {path.name for path in paths} == HOST_PACKAGES
    assert all(path.parent.name == "deploy" and path.is_dir() for path in paths)


def test_pacotes_do_host_cabem_no_piso() -> None:
    assert check_host_python.version_floor_report(check_host_python.host_paths()) == ""


def test_todo_modulo_do_host_importa() -> None:
    assert check_host_python.import_failures(check_host_python.host_paths()) == []


def test_modulo_que_quebra_ao_importar_e_relatado(tmp_path: Path) -> None:
    package = _package(tmp_path, "raise RuntimeError('quebrado')\n")

    failures = check_host_python.import_failures([package])

    assert failures == [f"{package.name}.modulo: RuntimeError: quebrado"]


def test_recusa_rodar_fora_do_python_do_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(check_host_python, "HOST_PYTHON", (3, 0))

    assert check_host_python.main([]) == check_host_python.WRONG_PYTHON_EXIT


def test_main_falha_com_api_acima_do_piso(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # O piso do vermin continua 3.12; só a trava do interpretador aceita o do teste.
    monkeypatch.setattr(check_host_python, "_running_python", lambda: check_host_python.HOST_PYTHON)
    package = _package(tmp_path, "import copy\n\nREPLACE = copy.replace\n")

    assert check_host_python.main([str(package)]) == 1
    assert "copy.replace" in capsys.readouterr().err
