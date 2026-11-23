"""zizmor da CI: instalado por hash e auditando `.github/` inteiro (#366, Renovação f).

`uvx zizmor@X` baixa do PyPI o que estiver publicado com aquele número, sem conferir hash, e o
alvo `.github/workflows` deixa o `dependabot.yml` fora dos audits `dependabot-*`. O script
`scripts/ci-zizmor.sh` é o portão local e o da CI: a versão e os hashes ficam num lugar só.

Nos testes do script, um `python3` falso no PATH cria, em `-m venv DIR`, um `pip` e um `zizmor`
falsos que gravam os argumentos num arquivo e saem com o status pedido.
"""

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "zizmor.yml"
REQUIREMENTS = ROOT / ".github" / "zizmor" / "requirements.txt"
SCRIPT = ROOT / "scripts" / "ci-zizmor.sh"
SCRIPT_TIMEOUT_S = 30

FAKE_PYTHON3 = r"""#!/usr/bin/env bash
if [[ $1 == -c ]]; then
  exit "${FAKE_ENSUREPIP_STATUS:-0}"
fi
if [[ $1 == -m && $2 == venv ]]; then
  mkdir -p "$3/bin"
  for ferramenta in pip zizmor; do
    cat > "$3/bin/$ferramenta" <<EOF
#!/usr/bin/env bash
printf '%s\n' "\$@" > "$FAKE_LOG_DIR/$ferramenta"
status_var=FAKE_\$(echo $ferramenta | tr a-z A-Z)_STATUS
exit "\${!status_var:-0}"
EOF
    chmod +x "$3/bin/$ferramenta"
  done
  exit 0
fi
echo "python3 falso: chamada inesperada: $*" >&2
exit 99
"""


@dataclass(frozen=True)
class Requisito:
    name: str
    version: str
    hashes: list[str]


@dataclass(frozen=True)
class Chamadas:
    returncode: int
    stderr: str
    pip: list[str] | None
    zizmor: list[str] | None


def _zizmor_workflow() -> dict[Any, Any]:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(workflow, dict)
    return workflow


def _zizmor_job() -> dict[str, Any]:
    job = _zizmor_workflow()["jobs"]["zizmor"]
    assert isinstance(job, dict)
    return job


def _requirements() -> list[Requisito]:
    """Linhas lógicas do requirements.txt, juntando as continuações com `\\`."""
    texto = REQUIREMENTS.read_text(encoding="utf-8").replace("\\\n", " ")
    requisitos = []
    for linha in texto.splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#"):
            continue
        spec, *opcoes = linha.split()
        name, version = spec.split("==")
        hashes = [o.removeprefix("--hash=sha256:") for o in opcoes if o.startswith("--hash=")]
        requisitos.append(Requisito(name, version, hashes))
    return requisitos


def _rodar_script_com_python3_falso(
    tmp_path: Path,
    *argumentos: str,
    pip_status: int = 0,
    ensurepip_status: int = 0,
) -> Chamadas:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    python3 = bin_dir / "python3"
    python3.write_text(FAKE_PYTHON3)
    python3.chmod(0o755)
    logs = tmp_path / "logs"
    logs.mkdir()
    env = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "ZIZMOR_VENV": str(tmp_path / "venv"),
        "FAKE_LOG_DIR": str(logs),
        "FAKE_PIP_STATUS": str(pip_status),
        "FAKE_ENSUREPIP_STATUS": str(ensurepip_status),
    }
    resultado = subprocess.run(  # noqa: S603 - script do repositório
        ["bash", str(SCRIPT), *argumentos],  # noqa: S607 - bash do PATH
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=SCRIPT_TIMEOUT_S,
        cwd=ROOT,
    )

    def lidas(ferramenta: str) -> list[str] | None:
        log = logs / ferramenta
        return log.read_text().splitlines() if log.exists() else None

    return Chamadas(resultado.returncode, resultado.stderr, lidas("pip"), lidas("zizmor"))


def test_workflow_roda_o_zizmor_pelo_script_travado() -> None:
    steps = _zizmor_job()["steps"]
    runs = [p.get("run", "") for p in steps]
    assert "scripts/ci-zizmor.sh --format=github" in runs
    assert not any("uvx" in r or "pip install" in r for r in runs)
    assert not any("setup-uv" in p.get("uses", "") for p in steps)


def test_zizmor_roda_quando_muda_o_que_ele_audita_ou_o_que_o_instala() -> None:
    gatilhos = _zizmor_workflow()[True]  # `on:` vira True no YAML 1.1
    esperado = [".github/**", "scripts/ci-zizmor.sh"]
    assert gatilhos["pull_request"]["paths"] == esperado
    assert gatilhos["push"]["paths"] == esperado


def test_requirements_do_zizmor_fixam_versao_e_hash() -> None:
    reqs = _requirements()
    assert [r.name for r in reqs] == ["zizmor"]
    assert re.fullmatch(r"\d+\.\d+\.\d+", reqs[0].version)
    assert len(reqs[0].hashes) >= 2
    assert all(re.fullmatch(r"[0-9a-f]{64}", h) for h in reqs[0].hashes)


def test_script_instala_com_hash_e_audita_github_inteiro(tmp_path: Path) -> None:
    chamadas = _rodar_script_com_python3_falso(tmp_path, "--format=github")
    assert chamadas.returncode == 0, chamadas.stderr
    assert chamadas.pip == [
        "install",
        "--require-hashes",
        "--no-deps",
        "--only-binary=:all:",
        "-r",
        ".github/zizmor/requirements.txt",
    ]
    assert chamadas.zizmor == ["--offline", "--format=github", ".github/"]


def test_script_para_se_a_instalacao_falhar(tmp_path: Path) -> None:
    resultado = _rodar_script_com_python3_falso(tmp_path, pip_status=1)
    assert resultado.pip is not None, "o script nem chegou a instalar"
    assert resultado.returncode != 0
    assert resultado.zizmor is None


def test_script_explica_quando_o_python3_nao_tem_venv(tmp_path: Path) -> None:
    resultado = _rodar_script_com_python3_falso(tmp_path, ensurepip_status=1)
    assert resultado.returncode != 0
    assert resultado.pip is None
    assert "ensurepip" in resultado.stderr
