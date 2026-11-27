"""Segredos do provisionamento do host (#442, 34e).

Os segredos nascem num temporário e só entram no lugar por `mv`, conferidos: um `openssl` que falha
não deixa arquivo vazio que a execução seguinte pularia. O script roda de verdade com um `openssl`
falso no PATH quando o teste precisa de falha.
"""

import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEPLOY = ROOT / "deploy"
PROVISION_SH = DEPLOY / "provision.sh"
SECRETS_SH = DEPLOY / "provision-secrets.sh"
HOMEBREW_BASH = Path("/opt/homebrew/bin/bash")
BASH = str(HOMEBREW_BASH) if HOMEBREW_BASH.exists() else "/bin/bash"
TIMEOUT_S = 60
APP_ENV_KEYS = ("OPENOBSERVE_ROOT_EMAIL", "OPENOBSERVE_ROOT_PASSWORD", "OPENOBSERVE_BASIC_AUTH")


def _fake(path: Path, body: str) -> None:
    path.write_text(f"#!/usr/bin/env bash\n{body}\n")
    path.chmod(0o755)


def _run(
    script: Path, *args: str, path: Path, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - argumentos montados pelo teste, sem shell
        [BASH, str(script), *args],
        env={"PATH": f"{path}:/usr/bin:/bin", **(env or {})},
        capture_output=True,
        text=True,
        timeout=TIMEOUT_S,
        check=False,
    )


# --- segredos do provisionamento -------------------------------------------------------------


def _openssl_que_falha_na_primeira(tmp_path: Path) -> Path:
    real = shutil.which("openssl")
    assert real, "openssl ausente"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    estado = tmp_path / "openssl-ja-falhou"
    _fake(
        bin_dir / "openssl",
        f'[[ -e "{estado}" ]] || {{ touch "{estado}"; exit 1; }}\nexec "{real}" "$@"',
    )
    return bin_dir


def _segredos(tmp_path: Path) -> Path:
    pasta = tmp_path / "secrets"
    pasta.mkdir(mode=0o700)
    return pasta


def test_openssl_que_falha_nao_deixa_segredo_vazio(tmp_path: Path) -> None:
    bin_dir = _openssl_que_falha_na_primeira(tmp_path)
    pasta = _segredos(tmp_path)

    primeira = _run(SECRETS_SH, str(pasta), path=bin_dir)

    assert primeira.returncode != 0
    assert list(pasta.iterdir()) == []

    segunda = _run(SECRETS_SH, str(pasta), path=bin_dir)

    assert segunda.returncode == 0, segunda.stderr
    assert sorted(p.name for p in pasta.iterdir()) == ["app.env", "db_password"]
    assert re.fullmatch(r"[0-9a-f]{64}\n", (pasta / "db_password").read_text())
    app_env = dict(linha.split("=", 1) for linha in (pasta / "app.env").read_text().splitlines())
    assert tuple(app_env) == APP_ENV_KEYS
    assert all(app_env.values())
    assert (pasta / "app.env").stat().st_mode & 0o777 == 0o600
    assert (pasta / "db_password").stat().st_mode & 0o777 == 0o644


def test_segredo_existente_nao_e_trocado(tmp_path: Path) -> None:
    pasta = _segredos(tmp_path)
    (pasta / "db_password").write_text("antiga\n")
    (pasta / "app.env").write_text("OPENOBSERVE_ROOT_EMAIL=quem@ja.existe\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _fake(bin_dir / "openssl", "exit 1")

    resultado = _run(SECRETS_SH, str(pasta), path=bin_dir)

    assert resultado.returncode == 0, resultado.stderr
    assert (pasta / "db_password").read_text() == "antiga\n"
    assert (pasta / "app.env").read_text() == "OPENOBSERVE_ROOT_EMAIL=quem@ja.existe\n"


def test_segredo_vazio_de_execucao_antiga_e_gerado_de_novo(tmp_path: Path) -> None:
    pasta = _segredos(tmp_path)
    (pasta / "db_password").write_text("")
    real = Path(shutil.which("openssl") or "/usr/bin/openssl").parent

    resultado = _run(SECRETS_SH, str(pasta), path=real)

    assert resultado.returncode == 0, resultado.stderr
    assert re.fullmatch(r"[0-9a-f]{64}\n", (pasta / "db_password").read_text())


def test_provision_delega_os_segredos_e_mantem_o_dono() -> None:
    texto = PROVISION_SH.read_text(encoding="utf-8")

    assert 'provision-secrets.sh" "$ROOT/secrets"' in texto
    assert texto.index("provision-secrets.sh") < texto.index(
        'chown "$APP_USER:$APP_USER" "$ROOT/secrets/db_password"'
    )
    assert "openssl rand" not in texto
