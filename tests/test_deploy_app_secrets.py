"""deploy/app-secrets.sh: segredos gerados pela aplicação no `app.env` (3.3 e 3.7).

Roda o script de verdade sobre um `app.env` temporário, como o release.sh faz na VPS.
"""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "deploy" / "app-secrets.sh"
GENERATED = ("GS_CURSOR_SECRET", "GS_USER_HASH_SECRET")
HEX_SECRET = re.compile(r"[0-9a-f]{64}")


def ensure_secrets(env_file: Path) -> None:
    subprocess.run(  # noqa: S603 - argumentos montados pelo teste, sem shell
        ["bash", str(SCRIPT), str(env_file)],  # noqa: S607
        check=True,
        capture_output=True,
    )


def entries(env_file: Path) -> dict[str, str]:
    lines = env_file.read_text().splitlines()
    return dict(line.split("=", 1) for line in lines)


def test_arquivo_sem_quebra_de_linha_final_nao_gruda_o_segredo_na_ultima_linha(
    tmp_path: Path,
) -> None:
    env_file = tmp_path / "app.env"
    env_file.write_text("GS_SMTP_HOST=smtp.example.com")

    ensure_secrets(env_file)

    values = entries(env_file)
    assert values["GS_SMTP_HOST"] == "smtp.example.com"
    for name in GENERATED:
        assert HEX_SECRET.fullmatch(values[name]), name
    assert env_file.read_text().endswith("\n")


def test_segredo_existente_e_mantido_e_rodar_de_novo_nao_duplica(tmp_path: Path) -> None:
    env_file = tmp_path / "app.env"
    env_file.write_text(f"GS_CURSOR_SECRET={'c' * 64}\n")

    ensure_secrets(env_file)
    first = env_file.read_text()
    ensure_secrets(env_file)

    assert env_file.read_text() == first
    assert entries(env_file)["GS_CURSOR_SECRET"] == "c" * 64
    assert sorted(entries(env_file)) == sorted(GENERATED)


def test_arquivo_vazio_recebe_os_dois_segredos(tmp_path: Path) -> None:
    env_file = tmp_path / "app.env"
    env_file.write_text("")

    ensure_secrets(env_file)

    assert sorted(entries(env_file)) == sorted(GENERATED)
    assert env_file.read_text().splitlines()[0].startswith("GS_")
