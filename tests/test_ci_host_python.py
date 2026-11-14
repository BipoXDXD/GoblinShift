"""O job "Scripts do host" roda no Python do host e a tag `:main` espera todos os portões.

O executor-ctl, o backup e os outros pacotes do host rodam no python3 do sistema (3.12), não no
Python da imagem. O job só protege se rodar sempre, no 3.12, com todo teste desses pacotes.
"""

import re
import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
CI = ROOT / ".github" / "workflows" / "ci.yml"
HOST_JOB = "host-python"
TAG_JOB = "tag-main"
HOST_PYTHON = "3.12"
CHECKER = "python scripts/check_host_python.py"
HOST_TEST_DIRS = (
    "tests/acceptance",
    "tests/executor_ctl",
    "tests/executor_inventory_read",
    "tests/host_python",
    "tests/tls_check",
)
# Pasta de testes de cada pacote py312 do pyproject; todo pacote novo do host entra aqui.
HOST_PACKAGE_TESTS = {
    "acceptance": "tests/acceptance",
    "backup": "tests/backup",
    "executor_ctl": "tests/executor_ctl",
    "executor_inventory": "tests/executor_inventory_read",
    "openobserve": "tests/openobserve",
    "tls_check": "tests/tls_check",
}
# Pastas de pacotes do host que ficam fora do job, e por quê. O checador ainda confere o piso
# de versão e a importação desses pacotes no 3.12.
NOT_HOST_DIRS = {
    "tests/backup": "o conftest sobe o PostgreSQL e importa a aplicação",
    "tests/openobserve": "precisa do OpenTelemetry e do OpenObserve real (job openobserve)",
}
HOST_MARKER = "pytest.mark.host_python"
# Módulos dessas pastas que não rodam no Python do host, e por quê.
NOT_HOST_PYTHON = {
    "tests/acceptance/test_chaos.py": "compara com o CSV Janitor, que é da aplicação",
    "tests/acceptance/test_install.py": "instalador como root no Debian (host_installer)",
    "tests/acceptance/test_media_mill_load.py": "lê o catálogo da aplicação",
    "tests/executor_ctl/test_area_busy.py": "testa o cliente do worker, que importa a aplicação",
    "tests/executor_ctl/test_client_deadlines.py": "confronta o cliente do worker com o servidor",
    "tests/executor_ctl/test_integration.py": "Docker real (marcador executor_ctl)",
    "tests/executor_ctl/test_capabilities.py": "instalador como root no Debian (host_installer)",
    "tests/executor_ctl/test_policy_native.py": "precisa de runner Linux com root",
}
# Jobs que não são portão da `:main`: o deploy legado, que nunca roda, e o que depende dele.
NOT_GATES = {"deploy", "vps-smoke", TAG_JOB}
MAIN_TAG = re.compile(r":main\b")
IMAGES = ("goblinshift", "goblinshift-executor", "goblinshift-web")


def _jobs() -> dict[str, dict[str, Any]]:
    document: dict[str, Any] = yaml.safe_load(CI.read_text())
    jobs: dict[str, dict[str, Any]] = document["jobs"]
    return jobs


def _runs(job_id: str) -> str:
    return "\n".join(step.get("run", "") for step in _jobs()[job_id]["steps"])


def _host_test_modules() -> list[str]:
    return sorted(
        str(path.relative_to(ROOT))
        for directory in HOST_TEST_DIRS
        for path in (ROOT / directory).rglob("test_*.py")
    )


def _host_packages() -> set[str]:
    document = tomllib.loads((ROOT / "pyproject.toml").read_text())
    targets: dict[str, str] = document["tool"]["ruff"]["per-file-target-version"]
    return {
        pattern.removeprefix("deploy/").removesuffix("/**")
        for pattern, target in targets.items()
        if target == "py312" and pattern.startswith("deploy/")
    }


def test_job_do_host_roda_sempre_e_sem_tolerar_falha() -> None:
    job = _jobs()[HOST_JOB]

    assert "if" not in job
    assert "continue-on-error" not in job
    assert all("continue-on-error" not in step and "if" not in step for step in job["steps"])


def test_job_do_host_usa_o_python_do_host() -> None:
    setup = next(
        step
        for step in _jobs()[HOST_JOB]["steps"]
        if step.get("uses", "").startswith("actions/setup-python@")
    )

    assert setup["with"] == {"python-version": HOST_PYTHON}


def test_job_do_host_instala_so_o_que_tem_hash() -> None:
    install = next(line for line in _runs(HOST_JOB).splitlines() if "pip install" in line)

    assert "--require-hashes" in install
    assert "-r tests/host_python/requirements.txt" in install


def test_job_do_host_roda_o_checador_e_os_testes_marcados() -> None:
    runs = _runs(HOST_JOB)

    assert CHECKER in runs.splitlines()
    assert "-m host_python" in runs
    assert all(directory in runs for directory in HOST_TEST_DIRS)


def test_todo_pacote_do_host_tem_seus_testes_no_job_ou_explica_por_que_nao() -> None:
    assert set(HOST_PACKAGE_TESTS) == _host_packages()
    for directory in HOST_PACKAGE_TESTS.values():
        assert (directory in HOST_TEST_DIRS) != (directory in NOT_HOST_DIRS), directory


@pytest.mark.parametrize("directory", HOST_TEST_DIRS)
def test_pasta_do_job_existe_e_tem_testes(directory: str) -> None:
    # Pasta renomeada some do grep do job sem erro; aqui ela reprova.
    assert any((ROOT / directory).rglob("test_*.py")), directory


@pytest.mark.parametrize("module", _host_test_modules())
def test_modulo_do_host_roda_no_python_do_host_ou_explica_por_que_nao(module: str) -> None:
    marked = HOST_MARKER in (ROOT / module).read_text()

    assert marked != (module in NOT_HOST_PYTHON)


def test_excecoes_apontam_para_modulos_que_existem() -> None:
    assert set(NOT_HOST_PYTHON) <= set(_host_test_modules())


def test_tag_main_so_no_job_que_espera_os_portoes() -> None:
    jobs = _jobs()
    with_main = {job_id for job_id, job in jobs.items() if MAIN_TAG.search(yaml.safe_dump(job))}

    assert with_main == {TAG_JOB}


def test_tag_main_espera_todos_os_portoes() -> None:
    jobs = _jobs()

    assert set(jobs[TAG_JOB]["needs"]) == set(jobs) - NOT_GATES


def test_tag_main_so_no_push_da_main() -> None:
    assert _jobs()[TAG_JOB]["if"] == (
        "github.event_name == 'push' && github.ref == 'refs/heads/main'"
    )


@pytest.mark.parametrize("image", IMAGES)
def test_tag_main_aponta_cada_imagem_para_a_tag_do_commit(image: str) -> None:
    runs = _runs(TAG_JOB)
    loop = next(line for line in runs.splitlines() if line.strip().startswith("for name in "))

    assert image in loop.removesuffix("; do").split()[3:]
    assert 'tag="sha-${GITHUB_SHA::12}"' in runs
    assert 'image="ghcr.io/bipoxdxd/$name"' in runs
    assert 'imagetools create --tag "$image:main" "$image:$tag"' in runs


def test_tag_main_confere_que_a_main_ficou_com_o_digest_do_commit() -> None:
    runs = [line.strip() for line in _runs(TAG_JOB).splitlines()]
    inspect = "docker buildx imagetools inspect --format '{{.Manifest.Digest}}'"

    assert f'esperado=$({inspect} "$image:$tag")' in runs
    assert f'obtido=$({inspect} "$image:main")' in runs
    assert 'if [ "$obtido" != "$esperado" ]; then' in runs
    assert "exit 1" in runs


def test_tag_main_so_pode_escrever_pacotes() -> None:
    assert _jobs()[TAG_JOB]["permissions"] == {"packages": "write"}
