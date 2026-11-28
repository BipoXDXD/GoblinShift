"""O job de contrato barra mudança incompatível em /api/v1 com `oasdiff breaking` (ADR 061, #442).

`docs/GoblinShiftPlan.md` só admite mudança aditiva em `/api/v1`. O teste de exportação confere
que a spec bate com o código, e o Schemathesis confere que a API bate com a spec, mas nenhum dos
dois compara a spec nova com a anterior. Os testes estáticos fixam o passo do CI, a imagem e os
argumentos que o script passa ao `docker` (com um `docker` falso no PATH); os marcados com
`oasdiff` rodam o script de verdade (precisam de Docker) contra pares de specs que quebram e que
não quebram o contrato.
"""

import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
CI = ROOT / ".github" / "workflows" / "ci.yml"
SCRIPT = ROOT / "scripts" / "openapi-breaking.sh"
ACEITAS = ROOT / "docs" / "api" / "quebras-aceitas.txt"
SPEC = ROOT / "web" / "openapi" / "openapi.json"
PRESET_RUNS = "/api/v1/presets/{preset_id}/runs"
ADR_DA_QUEBRA = re.compile(r"^# (?P<adr>docs/adr/\d{3}-[a-z0-9-]+\.md)$")
# Linha de --err-ignore da quebra de `_sem_status`: o método e o caminho do endpoint seguidos do
# texto que o oasdiff imprime para a mudança.
QUEBRA_DO_STATUS = (
    "GET /api/v1/runs removed the required property `data/items/status` "
    "from the response with the `200` status"
)
# `docker` falso: grava cada argumento recebido, separado por NUL, no arquivo de DOCKER_FALSO_LOG.
DOCKER_FALSO = '#!/bin/sh\nprintf \'%s\\0\' "$@" > "$DOCKER_FALSO_LOG"\n'

Spec = dict[str, Any]


def _jobs() -> dict[str, dict[str, Any]]:
    document: dict[str, Any] = yaml.safe_load(CI.read_text())
    jobs: dict[str, dict[str, Any]] = document["jobs"]
    return jobs


def _passos_do_contrato() -> list[dict[str, Any]]:
    passos: list[dict[str, Any]] = _jobs()["contract"]["steps"]
    return passos


def _passo_do_oasdiff() -> dict[str, Any]:
    return next(s for s in _passos_do_contrato() if "openapi-breaking.sh" in s.get("run", ""))


def _quebras_sem_adr(arquivo: Path, raiz: Path = ROOT) -> list[str]:
    """Entradas do arquivo de quebras aceitas sem um ADR existente logo acima.

    Cada linha que não é vazia nem comentário é uma entrada do `--err-ignore`. A linha anterior
    tem de ser `# docs/adr/NNN-<nome>.md`, e o ADR tem de existir em `raiz`.
    """
    linhas = arquivo.read_text(encoding="utf-8").splitlines()
    problemas = []
    for numero, linha in enumerate(linhas):
        if not linha.strip() or linha.startswith("#"):
            continue
        anterior = ADR_DA_QUEBRA.match(linhas[numero - 1]) if numero else None
        if anterior is None:
            problemas.append(f"linha {numero + 1} sem '# docs/adr/<arquivo>.md' logo acima")
        elif not (raiz / anterior["adr"]).is_file():
            problemas.append(f"linha {numero + 1} cita {anterior['adr']}, que não existe")
    return problemas


def test_checkout_do_contrato_traz_o_primeiro_pai() -> None:
    checkout = next(
        s for s in _passos_do_contrato() if s.get("uses", "").startswith("actions/checkout@")
    )
    assert checkout["with"]["fetch-depth"] == 2


def test_spec_anterior_vem_do_primeiro_pai() -> None:
    assert "git show HEAD^1:web/openapi/openapi.json" in _passo_do_oasdiff()["run"]


def test_passo_do_oasdiff_sempre_roda_e_reprova() -> None:
    passo = _passo_do_oasdiff()
    assert "if" not in passo
    assert "continue-on-error" not in passo


def test_imagem_do_oasdiff_fixada_por_digest() -> None:
    assert re.search(
        r'^OASDIFF_IMAGE="tufin/oasdiff:v[\d.]+@sha256:[0-9a-f]{64}"$', SCRIPT.read_text(), re.M
    )


def test_cada_quebra_aceita_cita_um_adr_existente() -> None:
    assert _quebras_sem_adr(ACEITAS) == []


@pytest.mark.parametrize(
    ("conteudo", "esperado"),
    [
        pytest.param(
            f"# cabeçalho\n\n# docs/adr/099-quebra.md\n{QUEBRA_DO_STATUS}\n",
            [],
            id="adr-existente-logo-acima-aceita",
        ),
        pytest.param(
            f"# docs/adr/099-quebra.md\n\n{QUEBRA_DO_STATUS}\n",
            ["linha 3 sem '# docs/adr/<arquivo>.md' logo acima"],
            id="adr-fora-da-linha-anterior-recusada",
        ),
        pytest.param(
            f"# docs/adr/098-outra.md\n{QUEBRA_DO_STATUS}\n",
            ["linha 2 cita docs/adr/098-outra.md, que não existe"],
            id="adr-inexistente-recusada",
        ),
    ],
)
def test_quebra_aceita_exige_adr_existente_logo_acima(
    tmp_path: Path, conteudo: str, esperado: list[str]
) -> None:
    (tmp_path / "docs" / "adr").mkdir(parents=True)
    (tmp_path / "docs" / "adr" / "099-quebra.md").write_text("# ADR 099\n")
    aceitas = tmp_path / "aceitas.txt"
    aceitas.write_text(conteudo)
    assert _quebras_sem_adr(aceitas, tmp_path) == esperado


@pytest.fixture
def base(tmp_path: Path) -> Path:
    destino = tmp_path / "base.json"
    destino.write_text(SPEC.read_text(encoding="utf-8"), encoding="utf-8")
    return destino


def _breaking(
    base: Path, head: Path, aceitas: Path | None = None, script: Path = SCRIPT
) -> subprocess.CompletedProcess[str]:
    argumentos = [str(script), str(base), str(head)]
    if aceitas is not None:
        argumentos.append(str(aceitas))
    return subprocess.run(  # noqa: S603 - script do próprio repositório, sem shell
        argumentos, capture_output=True, text=True, timeout=120, cwd=ROOT, check=False
    )


# Sem Docker: um `docker` falso no PATH grava os argumentos que o script passaria ao de verdade.


@pytest.fixture
def docker_falso(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Põe o `docker` falso na frente do PATH e devolve o arquivo onde ele grava os argumentos."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(DOCKER_FALSO)
    docker.chmod(0o755)
    log = tmp_path / "docker.log"
    monkeypatch.setenv("PATH", str(bin_dir), prepend=os.pathsep)
    monkeypatch.setenv("DOCKER_FALSO_LOG", str(log))
    return log


@pytest.fixture
def argumentos_do_docker(base: Path, docker_falso: Path) -> list[str]:
    result = _breaking(base, base)
    assert result.returncode == 0, result.stdout + result.stderr
    return docker_falso.read_text().split("\0")[:-1]


def test_oasdiff_roda_sem_rede(argumentos_do_docker: list[str]) -> None:
    # A spec nova vem do PR: com rede, um `$ref` externo faria o oasdiff buscar uma URL qualquer.
    assert argumentos_do_docker[argumentos_do_docker.index("--network") + 1] == "none"


def test_arquivos_entram_so_para_leitura(argumentos_do_docker: list[str]) -> None:
    montagens = [
        argumentos_do_docker[i + 1]
        for i, argumento in enumerate(argumentos_do_docker)
        if argumento == "--mount"
    ]
    assert len(montagens) == 4
    assert all(montagem.endswith(",readonly") for montagem in montagens), montagens


def test_oasdiff_recusa_refs_externas(argumentos_do_docker: list[str]) -> None:
    assert "--allow-external-refs=false" in argumentos_do_docker


def test_sem_arquivo_de_aceitas_falha_fechado(
    base: Path, docker_falso: Path, tmp_path: Path
) -> None:
    # Cópia do script fora do repositório: o padrão docs/api/quebras-aceitas.txt não existe lá.
    script = tmp_path / "repo" / "scripts" / "openapi-breaking.sh"
    script.parent.mkdir(parents=True)
    shutil.copy2(SCRIPT, script)
    result = _breaking(base, base, script=script)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "arquivo não encontrado: " in result.stderr
    assert result.stderr.rstrip().endswith("/docs/api/quebras-aceitas.txt")
    assert not docker_falso.exists()


# Com Docker: rodam o script, e portanto a imagem fixada nele.


def _spec_com(base: Path, mudar: Callable[[Spec], None]) -> Path:
    spec: Spec = json.loads(base.read_text(encoding="utf-8"))
    mudar(spec)
    head = base.with_name("head.json")
    head.write_text(json.dumps(spec), encoding="utf-8")
    return head


def _corpo_do_preset_run(spec: Spec) -> Spec:
    corpo: Spec = spec["paths"][PRESET_RUNS]["post"]["requestBody"]["content"]["application/json"]
    schema: Spec = corpo["schema"]
    return schema


def _nada(spec: Spec) -> None:
    """Não muda nada: a spec nova sai igual à anterior."""


def _sem_status(spec: Spec) -> None:
    run_response = spec["components"]["schemas"]["RunResponse"]
    del run_response["properties"]["status"]
    run_response["required"].remove("status")


def _obrigar_x_na_entrada(spec: Spec) -> None:
    schema = _corpo_do_preset_run(spec)
    schema["properties"]["x"] = {"type": "string"}
    schema["required"].append("x")


def _estreitar_entrada(spec: Spec) -> None:
    _corpo_do_preset_run(spec)["properties"]["input_upload_ids"]["maxItems"] = 10


def _sem_campo_opcional_na_resposta(spec: Spec) -> None:
    del spec["components"]["schemas"]["ProblemDetails"]["properties"]["errors_truncated"]


def _trocar_tipo_na_resposta(spec: Spec) -> None:
    spec["components"]["schemas"]["RunResponse"]["properties"]["attempt"]["type"] = "string"


def _campo_opcional_na_resposta(spec: Spec) -> None:
    spec["components"]["schemas"]["RunResponse"]["properties"]["novo"] = {"type": "string"}


def _valor_novo_em_enum_de_saida(spec: Spec) -> None:
    spec["components"]["schemas"]["ProblemCode"]["enum"].append("NOVO_CODIGO")


@pytest.mark.oasdiff
@pytest.mark.parametrize(
    ("mudar", "regra"),
    [
        pytest.param(
            _sem_status, "response-required-property-removed", id="obrigatorio-sai-da-resposta"
        ),
        pytest.param(
            _obrigar_x_na_entrada, "new-required-request-property", id="obrigatorio-na-entrada"
        ),
        pytest.param(
            _estreitar_entrada, "request-property-max-items-decreased", id="entrada-estreitada"
        ),
        pytest.param(
            _sem_campo_opcional_na_resposta,
            "response-optional-property-removed",
            id="opcional-sai-da-resposta",
        ),
        pytest.param(
            _trocar_tipo_na_resposta, "response-property-type-changed", id="tipo-da-resposta-muda"
        ),
    ],
)
def test_mudanca_incompativel_sai_com_erro(
    base: Path, mudar: Callable[[Spec], None], regra: str
) -> None:
    result = _breaking(base, _spec_com(base, mudar))
    assert result.returncode == 1, result.stdout + result.stderr
    assert f"error\t[{regra}]" in result.stdout


@pytest.mark.oasdiff
@pytest.mark.parametrize(
    ("mudar", "relatorio"),
    [
        pytest.param(_nada, "No changes detected", id="spec-igual"),
        pytest.param(_campo_opcional_na_resposta, "No breaking changes", id="opcional-na-resposta"),
        # Continua no relatório, como aviso: quem revisa o PR vê o valor novo.
        pytest.param(
            _valor_novo_em_enum_de_saida,
            "warning\t[response-property-enum-value-added]",
            id="valor-novo-em-enum-de-saida",
        ),
    ],
)
def test_mudanca_aditiva_passa(base: Path, mudar: Callable[[Spec], None], relatorio: str) -> None:
    result = _breaking(base, _spec_com(base, mudar))
    assert result.returncode == 0, result.stdout + result.stderr
    assert relatorio in result.stdout
    assert "error\t" not in result.stdout


@pytest.mark.oasdiff
def test_quebra_listada_no_arquivo_de_aceitas_passa(base: Path, tmp_path: Path) -> None:
    aceitas = tmp_path / "aceitas.txt"
    aceitas.write_text(f"# docs/adr/099-quebra.md\n{QUEBRA_DO_STATUS}\n", encoding="utf-8")
    result = _breaking(base, _spec_com(base, _sem_status), aceitas)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "No breaking changes" in result.stdout


@pytest.mark.oasdiff
def test_arquivo_de_aceitas_vazio_nao_aceita_nenhuma_quebra(base: Path, tmp_path: Path) -> None:
    aceitas = tmp_path / "aceitas.txt"
    aceitas.write_bytes(b"")
    result = _breaking(base, _spec_com(base, _sem_status), aceitas)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "error\t[response-required-property-removed]" in result.stdout
