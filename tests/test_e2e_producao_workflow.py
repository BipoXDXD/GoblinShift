"""Jornada com escrita em produção: depois da release e toda semana, sem desperdiçar a chave.

Roda código da `main` em cima de um gatilho `workflow_run`, com a chave de uma conta de teste que
escreve: nada do evento vira ref de checkout, a URL é fixa, e a chave só entra por `env` dos passos
que falam com a API. Falha avisa pelo healthchecks.io, como a restauração e o certificado.
"""

import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
WORKFLOW = WORKFLOWS / "e2e-producao.yml"
PRODUCTION = "https://goblinshift.dev"
PINNED_ACTION = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$")
SUNDAY_FIELDS = {"0", "7", "*"}
PING_URL = "https://hc-ping.invalid/check"


def workflow() -> dict[Any, Any]:
    loaded: dict[Any, Any] = yaml.safe_load(WORKFLOW.read_text())
    return loaded


def triggers() -> dict[str, Any]:
    # O PyYAML lê a chave `on` como o booleano True.
    on: dict[str, Any] = workflow()[True]
    return on


def journey() -> dict[str, Any]:
    job: dict[str, Any] = workflow()["jobs"]["journey"]
    return job


def notify() -> dict[str, Any]:
    job: dict[str, Any] = workflow()["jobs"]["notify"]
    return job


def step_named(name: str) -> dict[str, Any]:
    step: dict[str, Any] = next(step for step in journey()["steps"] if step.get("name") == name)
    return step


def step_index(name: str) -> int:
    return [step.get("name") for step in journey()["steps"]].index(name)


def test_roda_depois_da_release_toda_semana_e_a_mao() -> None:
    assert set(triggers()) == {"workflow_run", "schedule", "workflow_dispatch"}


def test_segue_o_workflow_da_release_pelo_nome_que_ele_tem() -> None:
    coolify = yaml.safe_load((WORKFLOWS / "coolify.yml").read_text())
    trigger = triggers()["workflow_run"]

    assert trigger["workflows"] == [coolify["name"]]
    assert trigger["types"] == ["completed"]
    assert trigger["branches"] == ["main"]


def test_so_a_release_bem_sucedida_da_main_deste_repositorio_dispara() -> None:
    # Expressão inteira: um `||` a mais abriria o job com todas as condições ainda presentes. O
    # `event` existe porque o coolify.yml também roda em PR, e um fork com branch `main` passaria
    # pelo filtro `branches`.
    assert " ".join(journey()["if"].split()) == (
        "github.ref == 'refs/heads/main' && "
        "(github.event_name != 'workflow_run' || "
        "(github.event.workflow_run.conclusion == 'success' && "
        "github.event.workflow_run.event != 'pull_request' && "
        "github.event.workflow_run.head_branch == 'main' && "
        "github.event.workflow_run.head_repository.full_name == github.repository))"
    )


def test_agenda_fora_do_domingo_da_aceitacao_que_para_o_worker() -> None:
    (entry,) = triggers()["schedule"]
    *_, day_of_week = entry["cron"].split()

    assert day_of_week not in SUNDAY_FIELDS


def test_nenhuma_url_vem_de_quem_dispara() -> None:
    assert triggers()["workflow_dispatch"] is None
    assert "inputs." not in WORKFLOW.read_text()
    assert journey()["env"]["BASE_URL"] == PRODUCTION


def test_nada_do_evento_escolhe_o_codigo_que_roda() -> None:
    checkout = next(s for s in journey()["steps"] if "actions/checkout" in s.get("uses", ""))

    assert "ref" not in checkout["with"]
    assert checkout["with"]["persist-credentials"] is False
    assert "head_sha" not in WORKFLOW.read_text()


def test_usa_o_environment_production_com_permissoes_minimas() -> None:
    assert journey()["environment"] == "production"
    assert workflow()["permissions"] == {"contents": "read"}
    assert "permissions" not in journey()
    assert notify()["permissions"] == {}


def test_toda_action_fica_fixada_por_sha() -> None:
    uses = [
        step["uses"]
        for job in workflow()["jobs"].values()
        for step in job["steps"]
        if "uses" in step
    ]

    assert uses
    for action in uses:
        assert PINNED_ACTION.match(action), action


def test_todo_job_tem_timeout() -> None:
    for name, job in workflow()["jobs"].items():
        assert job["timeout-minutes"] > 0, name


def test_duas_rodadas_nao_se_sobrepoem() -> None:
    concurrency = workflow()["concurrency"]

    assert concurrency["group"] == "e2e-producao"
    assert concurrency["cancel-in-progress"] is False


def test_python_vem_do_setup_python_como_nos_outros_jobs() -> None:
    steps = journey()["steps"]
    python = next(i for i, s in enumerate(steps) if "setup-python" in s.get("uses", ""))
    uv = next(i for i, s in enumerate(steps) if "setup-uv" in s.get("uses", ""))

    assert workflow()["env"]["UV_PYTHON_DOWNLOADS"] == "never"
    assert steps[python]["with"]["python-version-file"] == ".python-version"
    assert python < uv


def test_cache_do_uv_nao_entra_no_job_com_a_chave() -> None:
    setup_uv = next(s for s in journey()["steps"] if "setup-uv" in s.get("uses", ""))

    assert setup_uv["with"]["enable-cache"] is False


def test_a_chave_so_entra_por_env_dos_passos_que_falam_com_a_api() -> None:
    expected = "${{ secrets.E2E_VPS_API_KEY }}"
    holders = [s["name"] for s in journey()["steps"] if "GS_API_KEY" in s.get("env", {})]

    assert holders == ["Cota da conta de teste", "PDF Press", "Media Mill"]
    for name in holders:
        assert step_named(name)["env"]["GS_API_KEY"] == expected
    assert WORKFLOW.read_text().count("secrets.E2E_VPS_API_KEY") == len(holders)
    for job in workflow()["jobs"].values():
        for step in job["steps"]:
            assert "secrets." not in step.get("run", "")


def test_nao_sobe_artefato_nem_cita_a_chave_nos_comandos() -> None:
    for step in journey()["steps"]:
        assert "upload-artifact" not in step.get("uses", "")
        assert "$GS_API_KEY" not in step.get("run", "").replace('-z "$GS_API_KEY"', "")


def test_chave_ausente_falha_em_vez_de_pular_a_jornada() -> None:
    quota = step_named("Cota da conta de teste")["run"]

    assert '-z "$GS_API_KEY"' in quota
    assert "exit 1" in quota


def test_confere_a_cota_e_so_entao_roda_as_duas_receitas() -> None:
    quota = step_named("Cota da conta de teste")
    pdf = step_named("PDF Press")
    media = step_named("Media Mill")

    assert quota["id"] == "quota"
    assert 'scripts/e2e-quota-check.sh "$BASE_URL" >>"$GITHUB_OUTPUT"' in quota["run"]
    assert pdf["run"].strip() == 'scripts/e2e-pdf-press.sh "$BASE_URL"'
    assert media["run"].strip() == 'scripts/e2e-media-mill.sh "$BASE_URL"'
    assert pdf["if"] == "steps.quota.outputs.fits == 'true'"
    assert media["if"] == "steps.quota.outputs.fits == 'true'"
    assert journey()["outputs"]["fits"] == "${{ steps.quota.outputs.fits }}"
    assert step_index("Cota da conta de teste") < step_index("PDF Press") < step_index("Media Mill")


def test_o_ffmpeg_completo_chega_antes_das_fixtures_de_video() -> None:
    install = step_named("Instalar o FFmpeg completo (vídeos sintéticos do Media Mill)")

    assert "apt-get install -yq ffmpeg" in install["run"]
    assert step_index(install["name"]) < step_index("Media Mill")


def test_o_apt_nao_depende_do_espelho_do_azure_nem_consome_o_timeout_do_job() -> None:
    install = step_named("Instalar o FFmpeg completo (vídeos sintéticos do Media Mill)")
    run = install["run"]

    assert run.index("azure.archive.ubuntu.com") < run.index("apt-get update")
    assert "xargs -0 -r sudo sed -i" in run
    assert "/archive.ubuntu.com/g" in run
    assert install["timeout-minutes"] == 10


def test_o_aviso_roda_mesmo_com_a_jornada_quebrada_ou_estourada() -> None:
    assert notify()["needs"] == ["journey"]
    assert notify()["if"] == "always() && needs.journey.result != 'skipped'"


def test_o_aviso_pinga_sucesso_ou_fail_sem_a_url_na_linha_de_comando() -> None:
    (step,) = notify()["steps"]
    script = step["run"]

    assert step["env"]["E2E_WRITE_HEALTHCHECK_URL"] == "${{ secrets.E2E_WRITE_HEALTHCHECK_URL }}"
    assert step["env"]["RESULT"] == "${{ needs.journey.result }}"
    assert '[[ "$RESULT" == "success" ]] || suffix="/fail"' in script
    assert "curl --config -" in script


def run_notify(tmp_path: Path, url: str, result: str, fits: str) -> tuple[int, str]:
    """Roda o passo do aviso com um curl falso; devolve a saída e o stdin que o curl recebeu."""
    received = tmp_path / "curl-stdin"
    fake_curl = tmp_path / "curl"
    fake_curl.write_text(f'#!/bin/sh\ncat >"{received}"\n')
    fake_curl.chmod(0o755)
    (step,) = notify()["steps"]
    finished = subprocess.run(  # noqa: S603 - trecho do workflow do repositório
        ["/bin/bash", "-e", "-c", step["run"]],
        capture_output=True,
        text=True,
        check=False,
        env={
            "PATH": f"{tmp_path}:/usr/bin:/bin",
            "E2E_WRITE_HEALTHCHECK_URL": url,
            "RESULT": result,
            "FITS": fits,
        },
    )
    return finished.returncode, received.read_text() if received.exists() else ""


@pytest.mark.parametrize(
    ("result", "fits", "ping"),
    [
        ("success", "true", f'url = "{PING_URL}"\n'),
        ("failure", "true", f'url = "{PING_URL}/fail"\n'),
        ("failure", "", f'url = "{PING_URL}/fail"\n'),
        ("cancelled", "true", f'url = "{PING_URL}/fail"\n'),
    ],
)
def test_o_resultado_escolhe_o_ping(tmp_path: Path, result: str, fits: str, ping: str) -> None:
    status, received = run_notify(tmp_path, PING_URL, result, fits)

    assert status == 0
    assert received == ping


def test_rodada_pulada_por_cota_nao_pinga_sucesso(tmp_path: Path) -> None:
    status, received = run_notify(tmp_path, PING_URL, "success", "false")

    assert status == 0
    assert received == ""


def test_url_de_ping_ausente_vira_erro_visivel(tmp_path: Path) -> None:
    status, received = run_notify(tmp_path, "", "failure", "true")

    assert status != 0
    assert received == ""


def test_o_monitor_e_a_rotacao_da_chave_estao_documentados() -> None:
    monitors = (ROOT / "deploy" / "openobserve" / "README.md").read_text()
    readme = (ROOT / "README.md").read_text()

    assert re.search(
        r"^\| `jornada com escrita` \| healthchecks\.io \| 7 dias / 1 dia \|.*"
        r"`E2E_WRITE_HEALTHCHECK_URL`",
        monitors,
        re.M,
    )
    assert "2027-02-03" in readme
    assert "2027-02-03" in WORKFLOW.read_text()
