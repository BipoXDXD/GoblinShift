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
    "tests/executor_ctl/test_install_host.py": "instalador como root no Debian (host_installer)",
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
IMAGE_JOBS = [
    ("image", "app", "APP_DIGEST"),
    ("executor-image", "executor", "EXECUTOR_DIGEST"),
    ("web-image", "web", "WEB_DIGEST"),
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


def test_tag_main_espera_todos_os_portoes() -> None:
    jobs = _jobs()
    assert set(jobs[TAG_JOB]["needs"]) == set(jobs) - {TAG_JOB}
    assert "if" not in jobs[TAG_JOB]


@pytest.mark.parametrize(("job", "role", "env_var"), IMAGE_JOBS)
def test_tag_main_promove_os_digests_verificados_por_cada_build(
    job: str, role: str, env_var: str
) -> None:
    jobs = _jobs()
    image_job = jobs[job]
    assert "if" not in image_job
    assert image_job["outputs"]["digest"] == "${{ steps.build.outputs.digest }}"
    verify = next(
        step for step in image_job["steps"] if "scripts/ci-oci.sh stage" in step.get("run", "")
    )
    assert verify["env"]["INDEX_DIGEST"] == "${{ steps.build.outputs.digest }}"
    assert f'scripts/ci-oci.sh scan {role} "$INDEX_DIGEST"' in verify["run"]
    step = next(
        step
        for step in jobs[TAG_JOB]["steps"]
        if "scripts/ci-oci.sh promote" in step.get("run", "")
    )
    assert step["env"][env_var] == f"${{{{ needs.{job}.outputs.digest }}}}"
    assert "ci_oci.py record" in step["run"]


def test_promocao_real_espera_ci_da_main_e_confere_origem_dos_artefatos() -> None:
    release = yaml.safe_load(CI.with_name("coolify.yml").read_text())
    assert set(release[True]) == {"workflow_run", "workflow_dispatch"}
    assert set(release[True]["workflow_dispatch"]["inputs"]) == {"commit", "ci_run", "ci_attempt"}
    assert release["env"]["RESUME_CI_RUN_ID"] == "${{ inputs.ci_run }}"
    assert release["env"]["RESUME_CI_RUN_ATTEMPT"] == "${{ inputs.ci_attempt }}"
    jobs = release["jobs"]
    assert "github.event.workflow_run.event == 'push'" in jobs["gates"]["if"]
    assert (
        "github.event.workflow_run.head_repository.full_name == github.repository"
        in jobs["gates"]["if"]
    )
    gate_step = next(step for step in jobs["gates"]["steps"] if step.get("id") == "gates")
    assert gate_step["env"]["CI_RUN_ATTEMPT"] == "${{ github.event.workflow_run.run_attempt }}"
    publish = jobs["publish-images"]
    assert publish["needs"] == "gates"
    assert publish["if"] == "needs.gates.outputs.eligible == 'true'"
    assert publish["permissions"] == {"contents": "read", "actions": "read", "packages": "write"}
    assert "publish-images" in jobs["deploy"]["needs"]
    steps = {step["name"]: step for step in publish["steps"] if "name" in step}
    receipt = steps["Fetch the exact CI attempt receipt"]["with"]
    assert receipt["run-id"] == "${{ needs.gates.outputs.ci_run }}"
    assert receipt["name"] == "gs-validated-oci-${{ needs.gates.outputs.ci_attempt }}"
    assert steps["Fetch the OCI data admitted by the CI receipt"]["with"]["artifact-ids"] == (
        "${{ steps.receipt.outputs.ids }}"
    )
    sha = steps["Publish exactly the three already verified sha tags"]
    promote = steps["Promote the three indexes by digest and verify the tags"]
    assert sha["run"].index("publish-check") < sha["run"].index("publish-sha")
    assert sha["if"] == "steps.publishable.outputs.eligible == 'true'"
    assert promote["if"] == "steps.promotable.outputs.eligible == 'true'"

    scan = next(
        step
        for step in jobs["deploy"]["steps"]
        if step.get("name") == "Scan exact published images"
    )
    for role in ("app", "executor", "web"):
        assert (
            publish["outputs"][f"{role}_index"] == f"${{{{ steps.receipt.outputs.{role}_index }}}}"
        )
        assert (
            scan["env"][f"{role.upper()}_INDEX"]
            == f"${{{{ needs.publish-images.outputs.{role}_index }}}}"
        )
    assert 'crane manifest "$repository@$index"' in scan["run"]
    assert "ci_oci.py amd64" in scan["run"]


def test_artefatos_oci_de_tentativas_anteriores_conservam_seus_ids() -> None:
    jobs = _jobs()
    for job, role, _ in IMAGE_JOBS:
        upload = next(step for step in jobs[job]["steps"] if step.get("id") == "layout")
        assert upload["with"]["name"] == f"gs-oci-{role}-${{{{ github.run_attempt }}}}"
        assert "overwrite" not in upload["with"]
    receipt = next(
        step for step in jobs[TAG_JOB]["steps"] if "upload-artifact" in step.get("uses", "")
    )
    assert receipt["with"]["name"] == "gs-validated-oci-${{ github.run_attempt }}"
    assert "overwrite" not in receipt["with"]


def test_helper_de_publicacao_tem_o_mesmo_codeowner_dos_workflows() -> None:
    owners = (ROOT / ".github" / "CODEOWNERS").read_text().splitlines()
    rules = dict(line.split(maxsplit=1) for line in owners if line.startswith("/"))
    assert rules["/scripts/ci-oci.sh"] == rules["/.github/workflows/"]


def test_promocao_exige_o_digest_antes_e_depois_de_mover_a_tag() -> None:
    script = (ROOT / "scripts" / "ci-oci.sh").read_text()
    expected = 'verify_tag "sha-${commit:0:12}"'
    promotion = 'tag "$repository@$digest" main'
    assert script.index(expected) < script.index(promotion) < script.index("verify_tag main")
