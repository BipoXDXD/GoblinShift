"""Coerência entre as peças do backup: units, instalador, script e workflow (ADR 9xx)."""

import configparser
import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
BACKUP_DIR = ROOT / "deploy" / "backup"
APP_CHECKOUT = Path("/srv/goblinshift/app")
RESTORE_WORKFLOW = ROOT / ".github" / "workflows" / "restore-check.yml"
# `dono/repo@<sha de 40 dígitos>`, com a versão legível num comentário ao lado.
PINNED_ACTION = re.compile(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}")


def unit(name: str) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    parser.optionxform = str  # type: ignore[assignment,method-assign]
    parser.read(BACKUP_DIR / name)
    return parser


def required_by_script() -> set[str]:
    script = (BACKUP_DIR / "pg-backup.sh").read_text()
    match = re.search(r"^require_env ((?:.|\\\n)*?)\n(?!\s)", script, re.MULTILINE)
    assert match is not None
    return set(match.group(1).replace("\\\n", " ").split())


def required_by_installer() -> set[str]:
    installer = (BACKUP_DIR / "install.sh").read_text()
    match = re.search(r"^REQUIRED=\(([^)]*)\)", installer, re.MULTILINE)
    assert match is not None
    return set(match.group(1).split())


def test_servico_roda_como_usuario_da_aplicacao_com_o_script_do_checkout() -> None:
    service = unit("goblinshift-pg-backup.service")["Service"]

    assert service["User"] == "goblinshift"
    assert service["EnvironmentFile"] == "/srv/goblinshift/secrets/backup.env"
    script = Path(service["ExecStart"])
    assert (ROOT / script.relative_to(APP_CHECKOUT)).is_file()


def test_timer_e_diario_com_folga_e_recupera_execucao_perdida() -> None:
    timer = unit("goblinshift-pg-backup.timer")["Timer"]

    assert timer["OnCalendar"] == "*-*-* 06:00:00 UTC"
    assert timer["Persistent"] == "true"
    assert timer["RandomizedDelaySec"] == "30min"


def test_instalador_exige_o_que_o_script_exige_fora_o_que_a_unit_define() -> None:
    service = unit("goblinshift-pg-backup.service")["Service"]
    defined_by_unit = {service["Environment"].split("=", 1)[0]}

    assert required_by_installer() == required_by_script() - defined_by_unit


def restore_workflow() -> dict[str, Any]:
    workflow: dict[str, Any] = yaml.safe_load(RESTORE_WORKFLOW.read_text())
    return workflow


def steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    job_steps: list[dict[str, Any]] = job["steps"]
    return job_steps


def test_restauracao_usa_a_mesma_imagem_do_postgres_da_producao() -> None:
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text())

    restore_image = restore_workflow()["jobs"]["restore"]["env"]["RESTORE_PG_IMAGE"]
    assert restore_image == compose["services"]["postgres"]["image"]


def test_actions_da_restauracao_fixadas_por_sha_e_sem_credencial_persistida() -> None:
    for name, job in restore_workflow()["jobs"].items():
        for step in steps(job):
            if "uses" not in step:
                continue
            assert PINNED_ACTION.fullmatch(step["uses"]), (name, step["uses"])
            if step["uses"].startswith("actions/checkout@"):
                assert step["with"]["persist-credentials"] is False, name


def test_job_com_a_chave_privada_nao_roda_dependencias_do_projeto() -> None:
    """O `uv sync` instala código do PyPI: roda no job sem secrets, que repassa só o resultado."""
    jobs = restore_workflow()["jobs"]
    restore, revisions = jobs["restore"], jobs["revisions"]

    assert restore["environment"] == "backup-restore"
    restore_text = yaml.safe_dump(restore)
    assert "uv " not in restore_text
    assert "setup-uv" not in restore_text
    assert "secrets." not in yaml.safe_dump(revisions)
    assert "environment" not in revisions
    assert restore["needs"] == "revisions"
    known = restore_env_of(restore)["RESTORE_KNOWN_REVISIONS"]
    assert known == "${{ needs.revisions.outputs.known }}"


def test_postgres_da_restauracao_nao_loga_dados_das_linhas() -> None:
    restore = restore_workflow()["jobs"]["restore"]
    assert "services" not in restore
    start = next(step["run"] for step in steps(restore) if "docker run" in step.get("run", ""))

    assert "log_error_verbosity=terse" in start
    assert "log_min_error_statement=panic" in start
    assert "--publish" not in start
    assert " -p " not in start


def restore_env_of(job: dict[str, Any]) -> dict[str, str]:
    step = next(step for step in steps(job) if "pg-restore-check.sh" in step.get("run", ""))
    env: dict[str, str] = step["env"]
    return env


def test_repositorio_nunca_traz_chave_privada_age() -> None:
    for path in BACKUP_DIR.iterdir():
        assert "AGE-SECRET-KEY-" not in path.read_text(), path
