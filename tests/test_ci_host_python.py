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
CLEAN_IMPORTS = f"python -I -S -B {CHECKER.removeprefix('python ')} --imports-only"
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
ISOLATED_TEST_DIRS = ("tests/backup", "tests/openobserve")
HOST_MARKER = re.compile(r"^pytestmark = (\[)?pytest\.mark\.host_python\b", re.MULTILINE)
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
    "tests/backup/test_install.py": "instalador como root no Debian (host_installer)",
    "tests/backup/test_pg_backup.py": "integração com PostgreSQL e S3 local (backup)",
    "tests/openobserve/test_alert_firing.py": "integração com OpenObserve e Collector reais",
    "tests/openobserve/test_catalog.py": "compara a configuração com a aplicação",
    "tests/openobserve/test_cli.py": "inclui cenários com OpenObserve e Collector reais",
    "tests/openobserve/test_provision.py": "integração com OpenObserve e Collector reais",
    "tests/openobserve/test_saturation_runbook.py": "exercita a aplicação com uvicorn",
    "tests/openobserve/test_scheduler_round.py": "importa os cenários de integração do agendador",
    "tests/openobserve/test_sql.py": "integração de SQL com OpenObserve e Collector reais",
}
# Jobs que não são portão da `:main`: o deploy legado, que nunca roda, e o que depende dele.
NOT_GATES = {"deploy", "vps-smoke", TAG_JOB}
MAIN_TAG = re.compile(r":main\b")
IMAGE_JOBS = [
    ("image", "goblinshift", "APP_DIGEST"),
    ("executor-image", "goblinshift-executor", "EXECUTOR_DIGEST"),
    ("web-image", "goblinshift-web", "WEB_DIGEST"),
]


def _jobs() -> dict[str, dict[str, Any]]:
    document: dict[str, Any] = yaml.safe_load(CI.read_text())
    jobs: dict[str, dict[str, Any]] = document["jobs"]
    return jobs


def _runs(job_id: str) -> str:
    return "\n".join(step.get("run", "") for step in _jobs()[job_id]["steps"])


def _host_test_modules() -> list[str]:
    return sorted(
        str(path.relative_to(ROOT))
        for directory in (*HOST_TEST_DIRS, *ISOLATED_TEST_DIRS)
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


def test_imports_do_host_rodam_isolados_antes_de_instalar_dependencias() -> None:
    runs = _runs(HOST_JOB)

    assert CLEAN_IMPORTS in runs.splitlines()
    assert runs.index(CLEAN_IMPORTS) < runs.index("pip install")


def test_job_do_host_roda_o_checador_e_os_testes_marcados() -> None:
    runs = _runs(HOST_JOB)

    assert CHECKER in runs.splitlines()
    pytest_commands = [line for line in runs.splitlines() if line.startswith("python -m pytest ")]
    assert len(pytest_commands) == 2
    assert all("-p tests.skip_guard --fail-on-skip" in command for command in pytest_commands)
    assert "--confcutdir=tests/executor_ctl" in pytest_commands[0]
    assert '-m "host_python and not needs_root"' in pytest_commands[0]
    assert "--noconftest" in pytest_commands[1]
    assert '-m "host_python and not needs_root"' in pytest_commands[1]
    assert all(directory in runs for directory in (*HOST_TEST_DIRS, *ISOLATED_TEST_DIRS))


def test_todo_pacote_do_host_tem_testes_no_job() -> None:
    assert set(HOST_PACKAGE_TESTS) == _host_packages()
    assert set(HOST_PACKAGE_TESTS.values()) <= set(HOST_TEST_DIRS) | set(ISOLATED_TEST_DIRS)


@pytest.mark.parametrize("directory", (*HOST_TEST_DIRS, *ISOLATED_TEST_DIRS))
def test_pasta_do_job_existe_e_tem_testes(directory: str) -> None:
    assert any(
        HOST_MARKER.search(path.read_text()) for path in (ROOT / directory).rglob("test_*.py")
    ), directory


@pytest.mark.parametrize("module", _host_test_modules())
def test_modulo_do_host_roda_no_python_do_host_ou_explica_por_que_nao(module: str) -> None:
    marked = HOST_MARKER.search((ROOT / module).read_text()) is not None

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


@pytest.mark.parametrize(("job", "image", "env_var"), IMAGE_JOBS)
def test_tag_main_publica_os_digests_verificados_por_cada_build(
    job: str, image: str, env_var: str
) -> None:
    jobs = _jobs()
    image_job = jobs[job]
    assert image_job["outputs"]["digest"] == "${{ steps.build.outputs.digest }}"
    verify_step = next(
        step
        for step in image_job["steps"]
        if "scripts/docker-build.sh published" in step.get("run", "")
    )
    assert verify_step["env"]["IMAGE_REF"] == (
        f"ghcr.io/bipoxdxd/{image}@${{{{ steps.build.outputs.digest }}}}"
    )

    step = next(step for step in jobs[TAG_JOB]["steps"] if "run" in step)
    assert step["env"][env_var] == f"${{{{ needs.{job}.outputs.digest }}}}"
    assert image in step["run"]
    assert f'"${env_var}"' in step["run"]
    assert 'imagetools create --tag "$image:main" "$image@$esperado"' in step["run"]


def test_tag_main_confere_digests_antes_e_depois_da_promocao() -> None:
    runs = _runs(TAG_JOB)
    check = 'if [[ ! "$digest" =~ ^sha256:[a-f0-9]{64}$ ]]; then'
    assert runs.index(check) < runs.index("exit 1") < runs.index("imagetools create")
    assert 'esperado="${digests[index]}"' in runs
    inspect = "docker buildx imagetools inspect --format '{{.Manifest.Digest}}'"
    assert f'obtido=$({inspect} "$image:main")' in runs
    mismatch = 'if [ "$obtido" != "$esperado" ]; then'
    assert runs.index("imagetools create") < runs.index(mismatch) < runs.rindex("exit 1")


def test_tag_main_confere_a_ponta_antes_de_publicar() -> None:
    runs = _runs(TAG_JOB)
    assert 'gh api "repos/$GITHUB_REPOSITORY/git/ref/heads/main"' in runs
    stale = 'if [ "$ponta" != "$GITHUB_SHA" ]; then'
    assert runs.index(stale) < runs.index("exit 0") < runs.index("imagetools create")


def test_tag_main_so_pode_escrever_pacotes() -> None:
    assert _jobs()[TAG_JOB]["permissions"] == {"contents": "read", "packages": "write"}
