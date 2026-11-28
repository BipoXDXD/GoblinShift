"""O job de contrato barra mudança incompatível em /api/v1 com `oasdiff breaking` (ADR 061, #442).

`docs/GoblinShiftPlan.md` só admite mudança aditiva em `/api/v1`. O teste de exportação confere
que a spec bate com o código, e o Schemathesis confere que a API bate com a spec, mas nenhum dos
dois compara a spec nova com a anterior. Os testes estáticos fixam o passo do CI e a imagem; os
marcados com `oasdiff` rodam o script de verdade (precisam de Docker) contra pares de specs que
quebram e que não quebram o contrato.
"""

import copy
import json
import re
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
# Linha de --err-ignore da quebra de `test_spec_sem_campo_de_resposta_sai_com_erro`: o método e o
# caminho do endpoint seguidos do texto que o oasdiff imprime para a mudança.
QUEBRA_DO_STATUS = (
    "GET /api/v1/runs removed the required property `data/items/status` "
    "from the response with the `200` status"
)

Spec = dict[str, Any]


def _jobs() -> dict[str, dict[str, Any]]:
    document: dict[str, Any] = yaml.safe_load(CI.read_text())
    jobs: dict[str, dict[str, Any]] = document["jobs"]
    return jobs


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


def test_job_de_contrato_compara_com_o_primeiro_pai() -> None:
    job = _jobs()["contract"]
    checkout = next(s for s in job["steps"] if s.get("uses", "").startswith("actions/checkout@"))
    assert checkout["with"]["fetch-depth"] == 2
    step = next(s for s in job["steps"] if "openapi-breaking.sh" in s.get("run", ""))
    assert "HEAD^1:web/openapi/openapi.json" in step["run"]
    assert "if" not in step and "continue-on-error" not in step


def test_imagem_do_oasdiff_fixada_por_digest() -> None:
    assert re.search(
        r'^OASDIFF_IMAGE="tufin/oasdiff:v[\d.]+@sha256:[0-9a-f]{64}"$', SCRIPT.read_text(), re.M
    )


def test_oasdiff_roda_sem_rede_nem_refs_externas() -> None:
    # A spec nova vem do PR: um `$ref` externo faria o oasdiff buscar uma URL qualquer.
    script = SCRIPT.read_text()
    assert "--network none" in script
    assert "--allow-external-refs=false" in script


def test_cada_quebra_aceita_cita_um_adr_existente() -> None:
    assert _quebras_sem_adr(ACEITAS) == []


def test_quebra_com_adr_existente_logo_acima_e_aceita(tmp_path: Path) -> None:
    (tmp_path / "docs" / "adr").mkdir(parents=True)
    (tmp_path / "docs" / "adr" / "099-quebra.md").write_text("# ADR 099\n")
    aceitas = tmp_path / "aceitas.txt"
    aceitas.write_text(f"# cabeçalho\n\n# docs/adr/099-quebra.md\n{QUEBRA_DO_STATUS}\n")
    assert _quebras_sem_adr(aceitas, tmp_path) == []


def test_quebra_sem_adr_logo_acima_e_recusada(tmp_path: Path) -> None:
    (tmp_path / "docs" / "adr").mkdir(parents=True)
    (tmp_path / "docs" / "adr" / "099-quebra.md").write_text("# ADR 099\n")
    aceitas = tmp_path / "aceitas.txt"
    aceitas.write_text(f"# docs/adr/099-quebra.md\n\n{QUEBRA_DO_STATUS}\n")
    assert _quebras_sem_adr(aceitas, tmp_path) == [
        "linha 3 sem '# docs/adr/<arquivo>.md' logo acima"
    ]


def test_quebra_com_adr_inexistente_e_recusada(tmp_path: Path) -> None:
    aceitas = tmp_path / "aceitas.txt"
    aceitas.write_text(f"# docs/adr/099-quebra.md\n{QUEBRA_DO_STATUS}\n")
    assert _quebras_sem_adr(aceitas, tmp_path) == [
        "linha 2 cita docs/adr/099-quebra.md, que não existe"
    ]


# Com Docker: rodam o script, e portanto a imagem fixada nele.


@pytest.fixture
def base(tmp_path: Path) -> Path:
    destino = tmp_path / "base.json"
    destino.write_text(SPEC.read_text(encoding="utf-8"), encoding="utf-8")
    return destino


def _spec_com(base: Path, mudar: Callable[[Spec], None]) -> Path:
    spec: Spec = copy.deepcopy(json.loads(base.read_text(encoding="utf-8")))
    mudar(spec)
    head = base.with_name("head.json")
    head.write_text(json.dumps(spec), encoding="utf-8")
    return head


def _corpo_do_preset_run(spec: Spec) -> Spec:
    corpo: Spec = spec["paths"][PRESET_RUNS]["post"]["requestBody"]["content"]["application/json"]
    schema: Spec = corpo["schema"]
    return schema


def _breaking(
    base: Path, head: Path, aceitas: Path | None = None
) -> subprocess.CompletedProcess[str]:
    argumentos = [str(SCRIPT), str(base), str(head)]
    if aceitas is not None:
        argumentos.append(str(aceitas))
    return subprocess.run(  # noqa: S603 - script do próprio repositório, sem shell
        argumentos, capture_output=True, text=True, timeout=120, cwd=ROOT, check=False
    )


def _sem_status(spec: Spec) -> None:
    run_response = spec["components"]["schemas"]["RunResponse"]
    del run_response["properties"]["status"]
    run_response["required"].remove("status")


@pytest.mark.oasdiff
def test_spec_igual_passa(base: Path) -> None:
    result = _breaking(base, base)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "No changes detected" in result.stdout


@pytest.mark.oasdiff
def test_spec_sem_campo_de_resposta_sai_com_erro(base: Path) -> None:
    result = _breaking(base, _spec_com(base, _sem_status))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "[response-required-property-removed]" in result.stdout
    assert "in API GET /api/v1/runs" in result.stdout
    assert "`data/items/status`" in result.stdout


@pytest.mark.oasdiff
def test_campo_novo_obrigatorio_na_entrada_sai_com_erro(base: Path) -> None:
    def obrigar_x(spec: Spec) -> None:
        schema = _corpo_do_preset_run(spec)
        schema["properties"]["x"] = {"type": "string"}
        schema["required"].append("x")

    result = _breaking(base, _spec_com(base, obrigar_x))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "[new-required-request-property]" in result.stdout
    assert f"in API POST {PRESET_RUNS}" in result.stdout
    assert "required request property `x`" in result.stdout


@pytest.mark.oasdiff
def test_tipo_da_entrada_estreitado_sai_com_erro(base: Path) -> None:
    def estreitar(spec: Spec) -> None:
        _corpo_do_preset_run(spec)["properties"]["input_upload_ids"]["maxItems"] = 10

    result = _breaking(base, _spec_com(base, estreitar))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "[request-property-max-items-decreased]" in result.stdout
    assert f"in API POST {PRESET_RUNS}" in result.stdout


@pytest.mark.oasdiff
def test_campo_novo_opcional_na_resposta_passa(base: Path) -> None:
    def acrescentar(spec: Spec) -> None:
        spec["components"]["schemas"]["RunResponse"]["properties"]["novo"] = {"type": "string"}

    result = _breaking(base, _spec_com(base, acrescentar))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "No breaking changes" in result.stdout


@pytest.mark.oasdiff
def test_valor_novo_em_enum_de_saida_passa(base: Path) -> None:
    def acrescentar(spec: Spec) -> None:
        spec["components"]["schemas"]["ProblemCode"]["enum"].append("NOVO_CODIGO")

    result = _breaking(base, _spec_com(base, acrescentar))
    assert result.returncode == 0, result.stdout + result.stderr
    # Continua no relatório, como aviso: quem revisa o PR vê o valor novo.
    assert "warning\t[response-property-enum-value-added]" in result.stdout
    assert "error\t" not in result.stdout


@pytest.mark.oasdiff
def test_quebra_listada_no_arquivo_de_aceitas_passa(base: Path, tmp_path: Path) -> None:
    aceitas = tmp_path / "aceitas.txt"
    aceitas.write_text(f"# docs/adr/099-quebra.md\n{QUEBRA_DO_STATUS}\n", encoding="utf-8")
    result = _breaking(base, _spec_com(base, _sem_status), aceitas)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "No breaking changes" in result.stdout


@pytest.mark.oasdiff
def test_campo_opcional_removido_da_resposta_sai_com_erro(base: Path) -> None:
    def remover(spec: Spec) -> None:
        del spec["components"]["schemas"]["ProblemDetails"]["properties"]["errors_truncated"]

    result = _breaking(base, _spec_com(base, remover))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "error\t[response-optional-property-removed]" in result.stdout
