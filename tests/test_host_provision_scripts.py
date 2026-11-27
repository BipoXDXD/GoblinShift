"""Scripts de provisionamento do host: runsc, chaves apt e segredos (#442, 31e, 31f, 34e; #366).

- O host e a CI instalam o mesmo runsc, lido de `deploy/gvisor-version.env`; no host o pacote fica
  segurado (`apt-mark hold`), e uma versão diferente já instalada só gera aviso (D4).
- Toda chave apt é conferida pela impressão digital (`deploy/apt-key.sh`) antes de o repositório
  entrar nas fontes: o TLS sozinho não basta.
- Os segredos do provisionamento nascem num temporário e só entram no lugar por `mv`, conferidos:
  um `openssl` que falha não deixa arquivo vazio que a execução seguinte pularia.
- Todo download dos scripts do host tem prazo.

Os scripts rodam de verdade, copiados para um diretório temporário, com binários falsos no PATH
que gravam as chamadas: nenhum teste instala pacote nem mexe em /etc.
"""

import re
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DEPLOY = ROOT / "deploy"
GVISOR_SH = DEPLOY / "gvisor.sh"
VERSION_ENV = DEPLOY / "gvisor-version.env"
APT_KEY_SH = DEPLOY / "apt-key.sh"
PROVISION_SH = DEPLOY / "provision.sh"
SECRETS_SH = DEPLOY / "provision-secrets.sh"
CI_RUNSC_SH = ROOT / "scripts" / "ci-install-runsc.sh"
HOMEBREW_BASH = Path("/opt/homebrew/bin/bash")
BASH = str(HOMEBREW_BASH) if HOMEBREW_BASH.exists() else "/bin/bash"
TIMEOUT_S = 60
DOCKER_KEY_FINGERPRINT = "9DC858229FC7DD38854AE2D88D81803C0EBFCD88"
APP_ENV_KEYS = ("OPENOBSERVE_ROOT_EMAIL", "OPENOBSERVE_ROOT_PASSWORD", "OPENOBSERVE_BASIC_AUTH")
CURL_CALL = re.compile(r"(^|[|;&(]|&&|\|\|)\s*curl\s")


def _version_env() -> dict[str, str]:
    pares = (
        linha.split("=", 1)
        for linha in VERSION_ENV.read_text(encoding="utf-8").splitlines()
        if linha.strip() and not linha.lstrip().startswith("#")
    )
    return {chave.strip(): valor.strip().strip('"') for chave, valor in pares}


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


# --- versão do runsc -------------------------------------------------------------------------


def test_versao_do_runsc_fica_num_arquivo_so() -> None:
    versoes = _version_env()

    assert re.fullmatch(r"\d{8}", versoes["GVISOR_SUITE"])
    assert re.fullmatch(r"\d{8}\.\d+", versoes["GVISOR_VERSION"])
    assert versoes["GVISOR_VERSION"].startswith(versoes["GVISOR_SUITE"] + ".")
    assert re.fullmatch(r"[0-9A-F]{40}", versoes["GVISOR_KEY_FINGERPRINT"])


@pytest.mark.parametrize("script", [GVISOR_SH, CI_RUNSC_SH], ids=lambda p: p.name)
def test_ci_e_host_instalam_a_mesma_versao_do_runsc(script: Path) -> None:
    texto = script.read_text(encoding="utf-8")

    assert "gvisor-version.env" in texto and "apt-key.sh" in texto
    assert not re.search(r"^\s*(GVISOR_SUITE|GVISOR_VERSION|GVISOR_KEY_FINGERPRINT)=", texto, re.M)
    assert '"runsc=${GVISOR_VERSION}"' in texto
    assert "releases ${GVISOR_SUITE} main" in texto


# --- chaves apt ------------------------------------------------------------------------------


Chaves = dict[str, tuple[Path, str]]


def _gpg(gnupghome: Path, *args: str) -> bytes:
    env = {"GNUPGHOME": str(gnupghome), "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"}
    return subprocess.run(  # noqa: S603 - argumentos fixos do teste
        ["gpg", "--batch", *args],  # noqa: S607
        env=env,
        capture_output=True,
        check=True,
        timeout=TIMEOUT_S,
    ).stdout


@pytest.fixture(scope="module")
def chaves() -> Iterator[Chaves]:
    """Duas chaves descartáveis, geradas uma vez para o módulo.

    O GNUPGHOME fica num diretório temporário curto: o socket do gpg-agent tem limite de ~104
    caracteres no macOS, e o `tmp_path` do pytest passa disso.
    """
    raiz = Path(tempfile.mkdtemp(prefix="gs-gpg-"))
    home = raiz / "g"
    home.mkdir(mode=0o700)
    try:
        geradas: Chaves = {}
        subchaves: dict[str, list[str]] = {}
        for nome in ("boa", "intrusa"):
            uid = f"{nome} <{nome}@teste.invalid>"
            _gpg(
                home,
                "--pinentry-mode",
                "loopback",
                "--passphrase",
                "",
                "--quick-gen-key",
                uid,
                "ed25519",
                "sign",
                "never",
            )
            colunas = _gpg(home, "--with-colons", "--list-keys", uid).decode()
            primaria = next(
                linha.split(":")[9] for linha in colunas.splitlines() if linha.startswith("fpr:")
            )
            # Subchave de cifra: a impressão digital dela não pode valer como a da chave.
            _gpg(home, "--pinentry-mode", "loopback", "--passphrase", "", "--quick-add-key",
                 primaria, "cv25519", "encr", "never")  # fmt: skip
            colunas = _gpg(home, "--with-colons", "--list-keys", uid).decode()
            subchaves[nome] = [
                linha.split(":")[9] for linha in colunas.splitlines() if linha.startswith("fpr:")
            ][1:]
            fingerprint = next(
                linha.split(":")[9] for linha in colunas.splitlines() if linha.startswith("fpr:")
            )
            arquivo = raiz / f"{nome}.asc"
            arquivo.write_bytes(_gpg(home, "--armor", "--export", uid))
            geradas[nome] = (arquivo, fingerprint)
            assert len(subchaves[nome]) == 1
            geradas[f"{nome}-subchave"] = (arquivo, subchaves[nome][0])
        yield geradas
    finally:
        # Encerra o agente que o gpg subiu para este GNUPGHOME.
        subprocess.run(
            ["gpgconf", "--kill", "gpg-agent"],  # noqa: S607
            env={"GNUPGHOME": str(home), "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"},
            capture_output=True,
            check=False,
            timeout=TIMEOUT_S,
        )
        shutil.rmtree(raiz, ignore_errors=True)


def _verify(tmp_path: Path, arquivo: Path, fingerprint: str) -> subprocess.CompletedProcess[str]:
    script = tmp_path / "verify.sh"
    script.write_text(f'source "{APT_KEY_SH}"\nverify_apt_key "$1" "$2"\n')
    gpg_dir = Path(shutil.which("gpg") or "/usr/bin/gpg").parent
    home = Path(tempfile.mkdtemp(prefix="gs-gpgv-"))
    try:
        return _run(script, str(arquivo), fingerprint, path=gpg_dir, env={"GNUPGHOME": str(home)})
    finally:
        shutil.rmtree(home, ignore_errors=True)


def test_chave_apt_com_fingerprint_certo_e_aceita(tmp_path: Path, chaves: Chaves) -> None:
    arquivo, fingerprint = chaves["boa"]

    assert _verify(tmp_path, arquivo, fingerprint).returncode == 0


def test_chave_apt_com_fingerprint_diferente_e_recusada(tmp_path: Path, chaves: Chaves) -> None:
    arquivo, fingerprint = chaves["boa"]
    errado = fingerprint[:-1] + ("0" if fingerprint[-1] != "0" else "1")

    resultado = _verify(tmp_path, arquivo, errado)

    assert resultado.returncode != 0
    assert errado in resultado.stderr


def test_impressao_digital_da_subchave_e_recusada(tmp_path: Path, chaves: Chaves) -> None:
    arquivo, subchave = chaves["boa-subchave"]

    assert _verify(tmp_path, arquivo, subchave).returncode != 0


def test_arquivo_que_nao_e_chave_e_recusado(tmp_path: Path, chaves: Chaves) -> None:
    _, fingerprint = chaves["boa"]
    lixo = tmp_path / "lixo.asc"
    lixo.write_text("<html>não é uma chave</html>\n")

    assert _verify(tmp_path, lixo, fingerprint).returncode != 0


def test_impressao_digital_esperada_vazia_recusa_tudo(tmp_path: Path) -> None:
    lixo = tmp_path / "lixo.asc"
    lixo.write_text("<html>não é uma chave</html>\n")

    assert _verify(tmp_path, lixo, "").returncode != 0


def test_sem_gpg_a_recusa_diz_o_motivo(tmp_path: Path, chaves: Chaves) -> None:
    arquivo, fingerprint = chaves["boa"]
    script = tmp_path / "verify.sh"
    script.write_text(f'source "{APT_KEY_SH}"\nverify_apt_key "$1" "$2"\n')
    vazio = tmp_path / "sem-gpg"
    vazio.mkdir()
    (vazio / "awk").symlink_to(shutil.which("awk") or "/usr/bin/awk")

    resultado = subprocess.run(  # noqa: S603 - argumentos montados pelo teste, sem shell
        [BASH, str(script), str(arquivo), fingerprint],
        env={"PATH": str(vazio)},
        capture_output=True,
        text=True,
        timeout=TIMEOUT_S,
        check=False,
    )

    assert resultado.returncode != 0
    assert "gpg" in resultado.stderr and "impressão digital ''" not in resultado.stderr


def test_arquivo_com_chave_a_mais_e_recusado(tmp_path: Path, chaves: Chaves) -> None:
    boa, fingerprint = chaves["boa"]
    intrusa, _ = chaves["intrusa"]
    juntas = tmp_path / "juntas.asc"
    juntas.write_bytes(boa.read_bytes() + intrusa.read_bytes())

    assert _verify(tmp_path, juntas, fingerprint).returncode != 0


@pytest.mark.parametrize(
    ("script", "fonte"),
    [(GVISOR_SH, "gvisor.list"), (CI_RUNSC_SH, "gvisor.list"), (PROVISION_SH, "docker.sources")],
    ids=lambda v: v.name if isinstance(v, Path) else v,
)
def test_os_tres_scripts_conferem_a_chave_antes_de_confiar(script: Path, fonte: str) -> None:
    texto = script.read_text(encoding="utf-8")

    assert "verify_apt_key" in texto
    assert texto.index("verify_apt_key ") < texto.index(fonte)


def test_provision_confere_a_chave_do_docker_pela_impressao_digital() -> None:
    texto = PROVISION_SH.read_text(encoding="utf-8")

    assert f"DOCKER_KEY_FINGERPRINT={DOCKER_KEY_FINGERPRINT}" in texto
    assert 'verify_apt_key "$docker_key" "$DOCKER_KEY_FINGERPRINT"' in texto


# --- gvisor.sh rodando com binários falsos ---------------------------------------------------


def _run_gvisor(
    tmp_path: Path,
    *,
    installed: str | None,
    fingerprint: str | None = None,
    keyring_exists: bool = False,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    """Roda uma cópia do gvisor.sh com /etc e /usr/share trocados por diretórios do teste."""
    deploy = tmp_path / "deploy"
    deploy.mkdir()
    etc = tmp_path / "etc"
    (etc / "apt" / "sources.list.d").mkdir(parents=True)
    (etc / "docker").mkdir()
    (tmp_path / "keyrings").mkdir()
    if keyring_exists:
        (tmp_path / "keyrings" / "gvisor-archive-keyring.gpg").write_text("anel antigo\n")
    texto = (
        GVISOR_SH.read_text(encoding="utf-8")
        .replace("[[ $EUID -eq 0 ]]", "[[ 0 -eq 0 ]]")
        .replace("/etc/apt", str(etc / "apt"))
        .replace("/etc/docker", str(etc / "docker"))
        .replace("/usr/share/keyrings", str(tmp_path / "keyrings"))
    )
    (deploy / "gvisor.sh").write_text(texto)
    shutil.copy(VERSION_ENV, deploy)
    shutil.copy(APT_KEY_SH, deploy)

    calls = tmp_path / "calls.log"
    registra = f'echo "$(basename "$0") $*" >> "{calls}"'
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    instalado = "exit 1" if installed is None else "exit 0"
    _fake(
        bin_dir / "dpkg",
        f"{registra}\n[[ $1 == --print-architecture ]] && {{ echo amd64; exit 0; }}\n{instalado}",
    )
    _fake(bin_dir / "dpkg-query", f'{registra}\nprintf "%s" "{installed or ""}"')
    for nome in ("apt-get", "apt-mark", "systemctl"):
        _fake(bin_dir / nome, registra)
    _fake(
        bin_dir / "curl",
        f"{registra}\n"
        'while [[ $# -gt 0 ]]; do [[ $1 == -o ]] && { echo chave > "$2"; exit 0; }; shift; done\n'
        "echo chave",
    )
    fpr = fingerprint or _version_env()["GVISOR_KEY_FINGERPRINT"]
    _fake(
        bin_dir / "gpg",
        f"{registra}\n"
        'if [[ " $* " == *" --show-keys "* ]]; then\n'
        f'  printf "pub:-:4096:1:X:0:::-:::scSC:\\nfpr:::::::::{fpr}:\\n"; exit 0\n'
        "fi\n"
        'while [[ $# -gt 0 ]]; do [[ $1 == -o ]] && { echo anel > "$2"; exit 0; }; shift; done',
    )
    _fake(bin_dir / "docker", f'{registra}\necho \'{{"runc":{{}},"runsc":{{}}}}\'')
    _fake(bin_dir / "runsc", f'{registra}\necho "runsc version {installed or "nova"}"')

    resultado = _run(deploy / "gvisor.sh", path=bin_dir, env={"TMPDIR": str(tmp_path)})
    linhas = calls.read_text().splitlines() if calls.exists() else []
    return resultado, linhas


def _indice(linhas: list[str], prefixo: str) -> int:
    return next(i for i, linha in enumerate(linhas) if linha.startswith(prefixo))


def test_instalacao_nova_do_runsc_segura_o_pacote(tmp_path: Path) -> None:
    versao = _version_env()["GVISOR_VERSION"]

    resultado, linhas = _run_gvisor(tmp_path, installed=None)

    assert resultado.returncode == 0, resultado.stderr
    instala = _indice(linhas, f"apt-get install -yq runsc={versao}")
    assert instala < _indice(linhas, "apt-mark hold runsc")
    fonte = (tmp_path / "etc" / "apt" / "sources.list.d" / "gvisor.list").read_text()
    assert f"releases {_version_env()['GVISOR_SUITE']} main" in fonte


def test_chave_do_gvisor_errada_para_antes_da_fonte(tmp_path: Path) -> None:
    resultado, linhas = _run_gvisor(tmp_path, installed=None, fingerprint="0" * 40)

    assert resultado.returncode != 0
    assert not (tmp_path / "etc" / "apt" / "sources.list.d" / "gvisor.list").exists()
    assert not any(linha.startswith("apt-get install -yq runsc") for linha in linhas)


def test_anel_existente_com_chave_errada_para_antes_da_fonte(tmp_path: Path) -> None:
    resultado, linhas = _run_gvisor(
        tmp_path, installed=None, fingerprint="0" * 40, keyring_exists=True
    )

    assert resultado.returncode != 0
    assert not any(linha.startswith("curl") for linha in linhas)
    assert not (tmp_path / "etc" / "apt" / "sources.list.d" / "gvisor.list").exists()


def test_runsc_ja_instalado_passa_a_fonte_para_a_suite_fixada(tmp_path: Path) -> None:
    fonte = tmp_path / "etc" / "apt" / "sources.list.d" / "gvisor.list"

    resultado, _ = _run_gvisor(tmp_path, installed="20250101.0", keyring_exists=True)

    assert resultado.returncode == 0, resultado.stderr
    assert f"releases {_version_env()['GVISOR_SUITE']} main" in fonte.read_text()


def test_runsc_ja_instalado_na_versao_fixada_e_segurado(tmp_path: Path) -> None:
    resultado, linhas = _run_gvisor(tmp_path, installed=_version_env()["GVISOR_VERSION"])

    assert resultado.returncode == 0, resultado.stderr
    assert "apt-mark hold runsc" in linhas
    assert not any(linha.startswith("apt-get install") for linha in linhas)
    assert "aviso" not in resultado.stderr


def test_runsc_ja_instalado_em_outra_versao_avisa_e_nao_troca(tmp_path: Path) -> None:
    resultado, linhas = _run_gvisor(tmp_path, installed="20250101.0")

    assert resultado.returncode == 0, resultado.stderr
    assert "apt-mark hold runsc" in linhas
    assert not any(linha.startswith("apt-get install") for linha in linhas)
    assert "20250101.0" in resultado.stderr
    assert _version_env()["GVISOR_VERSION"] in resultado.stderr


# --- downloads com prazo ---------------------------------------------------------------------


@pytest.mark.parametrize("script", [GVISOR_SH, PROVISION_SH, CI_RUNSC_SH], ids=lambda p: p.name)
def test_todo_download_dos_scripts_do_host_tem_prazo(script: Path) -> None:
    chamadas = [
        linha
        for linha in script.read_text(encoding="utf-8").splitlines()
        if CURL_CALL.search(linha)
        and not linha.lstrip().startswith("#")
        and "apt-get install" not in linha
    ]
    assert chamadas

    assert [linha for linha in chamadas if "--max-time" not in linha] == []


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
