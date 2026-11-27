"""Imagens atestadas no build e conferidas antes de publicar no GHCR (#442, item 68c; D10).

Quem tem escrita no GHCR poderia trocar uma tag `sha-*`, e nada ligaria o digest ao run do CI que
o construiu e escaneou. Os jobs de imagem do `ci.yml` atestam a proveniência do índice (só no
push: no PR não há publicação) depois do scan, e o `publish-images` do `coolify.yml` confere o
atestado de cada índice pelo blob no layout OCI baixado, antes de qualquer escrita no registro.
"""

import re
from pathlib import Path
from typing import Any

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
