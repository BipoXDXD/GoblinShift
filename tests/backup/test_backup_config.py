"""Coerência entre as peças do backup: units, instalador, script e workflow (ADR 022)."""

import configparser
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
BACKUP_DIR = ROOT / "deploy" / "backup"
APP_CHECKOUT = Path("/srv/goblinshift/app")


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


def test_restauracao_usa_a_mesma_imagem_do_postgres_da_producao() -> None:
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text())
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "restore-check.yml").read_text())

    restore_image = workflow["jobs"]["restore"]["services"]["postgres"]["image"]
    assert restore_image == compose["services"]["postgres"]["image"]


def test_repositorio_nunca_traz_chave_privada_age() -> None:
    for path in BACKUP_DIR.iterdir():
        assert "AGE-SECRET-KEY-" not in path.read_text(), path
