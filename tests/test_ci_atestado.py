"""Imagens atestadas no build e conferidas antes de publicar no GHCR (#442, item 68c; D10).

Quem tem escrita no GHCR poderia trocar uma tag `sha-*`, e nada ligaria o digest ao run do CI que
o construiu e escaneou. Os jobs de imagem do `ci.yml` atestam a proveniência do índice (só no
push: no PR não há publicação) depois do scan, e o `publish-images` do `coolify.yml` confere o
atestado de cada índice pelo blob no layout OCI baixado, antes de qualquer escrita no registro.

Os mesmos jobs geram um SBOM SPDX por plataforma com o Trivy (`ci-oci.sh sbom`) e o atestam no
índice (#366, L11 h; ADR 075). O SBOM é informativo: a publicação confere só a proveniência.
"""

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
ATTEST = re.compile(r"^actions/attest-build-provenance@[0-9a-f]{40}$")
SUBJECTS = {
    "image": "ghcr.io/bipoxdxd/goblinshift",
    "executor-image": "ghcr.io/bipoxdxd/goblinshift-executor",
    "web-image": "ghcr.io/bipoxdxd/goblinshift-web",
}
BUILD_PERMISSIONS = {"contents": "read", "id-token": "write", "attestations": "write"}


def _workflow(nome: str) -> dict[str, Any]:
    dados: dict[str, Any] = yaml.safe_load((WORKFLOWS / nome).read_text(encoding="utf-8"))
    return dados


def _indice(passos: list[dict[str, Any]], condicao: Any) -> int:
    indices = [i for i, passo in enumerate(passos) if condicao(passo)]
    assert len(indices) == 1, indices
    return indices[0]


def _jobs_que_constroem() -> dict[str, dict[str, Any]]:
    return {
        nome: job
        for nome, job in _workflow("ci.yml")["jobs"].items()
        if any(
            passo.get("uses", "").startswith("docker/build-push-action@")
            for passo in job.get("steps", [])
        )
    }


def test_os_tres_jobs_de_imagem_constroem() -> None:
    assert set(_jobs_que_constroem()) == set(SUBJECTS)


def test_todo_job_que_constroi_imagem_atesta_o_digest_depois_do_scan() -> None:
    for nome, job in _jobs_que_constroem().items():
        passos = job["steps"]
        build = _indice(passos, lambda p: p.get("id") == "build")
        scan = _indice(passos, lambda p: "ci-oci.sh scan" in p.get("run", ""))
        atestado = _indice(passos, lambda p: bool(ATTEST.fullmatch(p.get("uses", ""))))
        passo = passos[atestado]

        assert build < scan < atestado, nome
        assert passo.get("if") == "github.event_name == 'push'", nome
        assert passo["with"] == {
            "subject-name": SUBJECTS[nome],
            "subject-digest": "${{ steps.build.outputs.digest }}",
            "push-to-registry": False,
        }, nome
        assert job.get("permissions") == BUILD_PERMISSIONS, nome


def test_so_os_jobs_de_imagem_podem_emitir_token_oidc_e_atestar() -> None:
    ci = _workflow("ci.yml")
    assert ci["permissions"] == {"contents": "read"}

    com_escrita = {
        nome
        for nome, job in ci["jobs"].items()
        if {"id-token", "attestations"} & set(job.get("permissions") or {})
    }

    assert com_escrita == set(SUBJECTS)


def test_publicacao_verifica_o_atestado_antes_de_gravar_no_ghcr() -> None:
    job = _workflow("coolify.yml")["jobs"]["publish-images"]
    passos = job["steps"]
    verifica = _indice(passos, lambda p: "gh attestation verify" in p.get("run", ""))
    publica = _indice(passos, lambda p: "publish-sha" in p.get("run", ""))
    promove = _indice(passos, lambda p: "publish-main" in p.get("run", ""))
    login = _indice(passos, lambda p: "crane auth login" in p.get("run", ""))
    passo = passos[verifica]

    assert verifica < min(publica, promove, login)
    assert job["permissions"].get("attestations") == "read"
    assert passo.get("env", {}).get("GH_TOKEN") == "${{ github.token }}"
    comando = " ".join(passo["run"].split())
    assert '--repo "$GITHUB_REPOSITORY"' in comando
    assert '--signer-workflow "$GITHUB_REPOSITORY/.github/workflows/ci.yml"' in comando
    assert "--source-ref refs/heads/main" in comando
    assert "for role in app executor web" in comando
    # O sujeito é o blob do índice com o digest do recibo, e o atestado é o do commit liberado.
    assert "jq -er --arg role \"$role\" '.images[$role].digest'" in comando
    assert 'gh attestation verify "$layout/blobs/sha256/${digest#sha256:}"' in comando
    assert '--source-digest "$RELEASE_COMMIT"' in comando
    assert job.get("env", {}).get("RELEASE_COMMIT") == "${{ needs.gates.outputs.commit }}"


# SBOM SPDX por plataforma, gerado pelo Trivy e atestado no índice (#366, L11 h; ADR 075).
ATTEST_SBOM = re.compile(r"^actions/attest-sbom@[0-9a-f]{40}$")
ROLES = {"image": "app", "executor-image": "executor", "web-image": "web"}


def _passos_attest_sbom(passos: list[dict[str, Any]]) -> list[int]:
    return [
        i for i, p in enumerate(passos) if str(p.get("uses", "")).startswith("actions/attest-sbom@")
    ]


def test_todo_job_que_constroi_imagem_atesta_um_sbom_por_plataforma() -> None:
    for nome, job in _jobs_que_constroem().items():
        passos = job["steps"]
        sboms = [passos[i] for i in _passos_attest_sbom(passos)]

        assert len(sboms) == 2, nome
        assert {p["with"]["sbom-path"] for p in sboms} == {
            f"${{{{ runner.temp }}}}/sbom-{ROLES[nome]}-{arch}.spdx.json"
            for arch in ("amd64", "arm64")
        }, nome
        for p in sboms:
            assert p["with"]["subject-name"] == SUBJECTS[nome], nome
            assert p["with"]["subject-digest"] == "${{ steps.build.outputs.digest }}", nome
            assert p["with"]["push-to-registry"] is False, nome
            assert p.get("if") == "github.event_name == 'push'", nome
            assert ATTEST_SBOM.fullmatch(p["uses"]), nome


def test_sbom_e_gerado_antes_de_atestar_e_nao_entra_na_verificacao() -> None:
    for nome, job in _jobs_que_constroem().items():
        passos = job["steps"]
        scan = _indice(passos, lambda p: "ci-oci.sh scan" in p.get("run", ""))
        gera = _indice(passos, lambda p: "ci-oci.sh sbom" in p.get("run", ""))
        atestados = _passos_attest_sbom(passos)
        layout = _indice(passos, lambda p: p.get("id") == "layout")

        assert atestados, nome
        assert scan < gera < min(atestados), nome
        # Falha do Trivy ou SBOM inválido derruba o job antes de subir o layout para publicação.
        assert max(atestados) < layout, nome
        assert f'ci-oci.sh sbom {ROLES[nome]} "$INDEX_DIGEST"' in passos[gera]["run"], nome
        assert "continue-on-error" not in passos[gera], nome
        assert all("continue-on-error" not in passos[i] for i in atestados), nome

    # Só a proveniência bloqueia a publicação: nenhum verify pede o predicado do SBOM.
    verificacoes = [
        passo["run"]
        for passo in _workflow("coolify.yml")["jobs"]["publish-images"]["steps"]
        if "gh attestation verify" in passo.get("run", "")
    ]
    assert verificacoes
    for comando in verificacoes:
        assert "--predicate-type" not in comando
        assert "spdx" not in comando.lower()


# `ci-oci.sh sbom` com um `docker` falso no PATH: ele grava os argumentos e escreve no diretório
# montado em `/out` um SPDX com FAKE_PACKAGES pacotes (e FAKE_PAD_BYTES de enchimento), ou sai
# com FAKE_DOCKER_STATUS sem escrever nada.
SCRIPT = ROOT / "scripts" / "ci-oci.sh"
DIGEST = "sha256:" + "a" * 64
FAKE_DOCKER = r"""#!/usr/bin/env bash
printf '%s\n' "$@" > "$FAKE_LOG"
[[ "${FAKE_DOCKER_STATUS:-0}" == 0 ]] || exit "$FAKE_DOCKER_STATUS"
out_dir='' output=''
while [[ $# -gt 0 ]]; do
  case "$1" in
    --volume) out_dir=${2%%:/out}; shift ;;
    --output) output=${2#/out/}; shift ;;
  esac
  shift
done
python3 - "$out_dir/$output" <<PY
import json, sys
pacotes = [{"name": f"p{i}"} for i in range(int("${FAKE_PACKAGES:-3}"))]
doc = {"spdxVersion": "SPDX-2.3", "packages": pacotes, "pad": "x" * int("${FAKE_PAD_BYTES:-0}")}
open(sys.argv[1], "w").write(json.dumps(doc))
PY
"""


def _sbom(
    tmp_path: Path, platform: str = "linux/arm64", **env: str
) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    docker = bin_dir / "docker"
    docker.write_text(FAKE_DOCKER, encoding="utf-8")
    docker.chmod(0o755)
    runner_temp = tmp_path / "runner"
    runner_temp.mkdir(exist_ok=True)
    ambiente = {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "GITHUB_ACTIONS": "true",
        "GITHUB_SHA": "b" * 40,
        "RUNNER_TEMP": str(runner_temp),
        "FAKE_LOG": str(tmp_path / "docker.log"),
        **env,
    }
    return subprocess.run(  # noqa: S603 - script do repositório
        ["bash", str(SCRIPT), "sbom", "app", DIGEST, platform],  # noqa: S607 - bash do PATH
        env=ambiente,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_sbom_grava_o_spdx_da_plataforma_a_partir_do_indice_no_registro(tmp_path: Path) -> None:
    resultado = _sbom(tmp_path)

    assert resultado.returncode == 0, resultado.stderr
    sbom = tmp_path / "runner" / "sbom-app-arm64.spdx.json"
    assert len(json.loads(sbom.read_text())["packages"]) == 3
    argumentos = (tmp_path / "docker.log").read_text().split("\n")
    assert argumentos[-2] == f"127.0.0.1:5000/goblinshift@{DIGEST}"
    comando = " ".join(argumentos)
    assert "--platform linux/arm64" in comando
    assert "--format spdx-json" in comando
    assert "--output /out/sbom-app-arm64.spdx.json" in comando
    assert f"--volume {tmp_path / 'runner'}:/out" in comando


@pytest.mark.parametrize(
    "env",
    [
        {"FAKE_DOCKER_STATUS": "1"},
        {"FAKE_PACKAGES": "1"},
        {"FAKE_PACKAGES": "3", "FAKE_PAD_BYTES": str(16 * 1024 * 1024)},
    ],
    ids=["trivy-falha", "sbom-sem-pacotes", "sbom-acima-de-16-mib"],
)
def test_sbom_invalido_ou_trivy_com_erro_falha_alto(tmp_path: Path, env: dict[str, str]) -> None:
    resultado = _sbom(tmp_path, **env)

    assert resultado.returncode != 0
    assert "::error::" in resultado.stderr


def test_sbom_recusa_plataforma_fora_do_release(tmp_path: Path) -> None:
    resultado = _sbom(tmp_path, platform="linux/riscv64")

    assert resultado.returncode != 0
    assert "::error::" in resultado.stderr
    assert not (tmp_path / "docker.log").exists()
