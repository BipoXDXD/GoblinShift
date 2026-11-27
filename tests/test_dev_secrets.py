"""`scripts/dev-secrets.sh` gera os segredos locais no formato que os contêineres leem (#442)."""

import base64
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "dev-secrets.sh"
# `$` seria expandido pelo `-c` do OpenObserve sem aspas simples.
LEGACY_PASSWORD = "Ab1_$HOME#z"  # noqa: S105 - senha de teste
EMAIL = "root@example.com"


def _run(project: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - argumentos montados pelo teste, sem shell
        ["bash", str(project / "scripts" / "dev-secrets.sh")],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "scripts").mkdir()
    shutil.copy(SCRIPT, tmp_path / "scripts" / "dev-secrets.sh")
    return tmp_path


def test_senha_do_env_antigo_vai_entre_aspas_simples_e_o_basic_auth_bate(project: Path) -> None:
    (project / ".env").write_text(
        f"OPENOBSERVE_ROOT_EMAIL={EMAIL}\nOPENOBSERVE_ROOT_PASSWORD={LEGACY_PASSWORD}\n"
    )

    result = _run(project)

    assert result.returncode == 0, result.stderr
    secrets_dir = project / ".secrets"
    env = (secrets_dir / "openobserve_env").read_text()
    assert env == f"ZO_ROOT_USER_PASSWORD='{LEGACY_PASSWORD}'\n"
    basic = (secrets_dir / "openobserve_basic_auth").read_text()
    assert base64.b64decode(basic).decode() == f"{EMAIL}:{LEGACY_PASSWORD}"
    assert not basic.endswith("\n")


def test_senha_com_aspas_simples_e_recusada(project: Path) -> None:
    (project / ".env").write_text(
        f"OPENOBSERVE_ROOT_EMAIL={EMAIL}\nOPENOBSERVE_ROOT_PASSWORD=Ab1_'x\n"
    )

    result = _run(project)

    assert result.returncode != 0
    assert "aspas simples" in result.stderr
    assert not (project / ".secrets" / "openobserve_env").exists()


def test_rodar_de_novo_mantem_os_segredos(project: Path) -> None:
    assert _run(project).returncode == 0
    before = {p.name: p.read_text() for p in (project / ".secrets").iterdir()}

    assert _run(project).returncode == 0

    assert {p.name: p.read_text() for p in (project / ".secrets").iterdir()} == before
