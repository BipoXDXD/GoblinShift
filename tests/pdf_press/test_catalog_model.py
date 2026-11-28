"""Modelo dos parâmetros do pdf-press v1 e v2 e conferência das referências a uploads (1.5)."""

import hashlib
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pikepdf
import pytest
from pydantic import ValidationError

from goblinshift.catalog import CATALOG, InvalidParametersError, VersionStatus
from goblinshift.catalog.pdf_press_v1 import PdfPressV1Parameters, upload_references
from goblinshift.catalog.references import UploadReference
from goblinshift.catalog.versions import PDF_PRESS_V2_MERGE_DEADLINE, sunset_after_merge
from goblinshift.processing.output_dir import FileSystemOutputDir
from goblinshift.processing.pdf_press import (
    MAX_INPUT_PAGES,
    MAX_OUTPUT_PAGES,
    Compression,
    Operation,
    PageRange,
    Params,
    params_from_mapping,
    params_v2_from_mapping,
)
from goblinshift.processing.recipe import InputFile
from goblinshift.processing.registry import RECIPES
from goblinshift.runs.acceptance import InvalidRunCommandError, RunCommand, validate_run
from tests.pdf_press import samples
from tests.pdf_press.conftest import PDF_PRESS_RESERVED_NAMES, RecordedProgress

UPLOAD_A = "4eb3e442-3f30-4d83-8d34-cb18a99a46cb"
UPLOAD_B = "1b8f283e-32ee-4687-9bd1-ae2f75f93cc2"


def errors(raw: object) -> list[tuple[str, ...]]:
    with pytest.raises(ValidationError) as caught:
        PdfPressV1Parameters.model_validate(raw)
    return [tuple(str(part) for part in error["loc"]) for error in caught.value.errors()]


def selection(*pages: tuple[str, int]) -> dict[str, object]:
    return {
        "operation": "select",
        "select": [{"upload_id": upload, "page_number": page} for upload, page in pages],
    }


def test_corpo_vazio_resolve_para_os_defaults_em_forma_canonica() -> None:
    assert CATALOG.resolve("pdf-press", 1, {}) == {
        "operation": "merge",
        "pages_per_document": 1,
        "select": None,
    }


def test_limites_publicados_no_catalogo() -> None:
    limits = CATALOG.get_version("pdf-press", 1).limits

    assert limits == {
        "max_inputs": 20,
        "max_input_bytes": 32 * 1024 * 1024,
        "max_total_input_bytes": 256 * 1024 * 1024,
        "max_input_pages": 200,
        "max_output_pages": 400,
        "max_output_documents": 200,
    }


def test_select_resolvido_normaliza_o_uuid_e_vira_os_params_da_biblioteca() -> None:
    resolved = CATALOG.resolve("pdf-press", 1, selection((UPLOAD_A.upper(), 2), (UPLOAD_A, 2)))

    assert params_from_mapping(resolved) == Params(
        operation=Operation.SELECT,
        select=(PageRange.single(UPLOAD_A, 2), PageRange.single(UPLOAD_A, 2)),
    )


def test_split_com_grupo_explicito_e_aceito() -> None:
    resolved = CATALOG.resolve("pdf-press", 1, {"operation": "split", "pages_per_document": 5})

    assert resolved["pages_per_document"] == 5


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param({"pages_per_document": 2}, id="merge-por-default"),
        pytest.param({"operation": "merge", "pages_per_document": 1}, id="igual-ao-default"),
        pytest.param({**selection((UPLOAD_A, 1)), "pages_per_document": 1}, id="no-select"),
    ],
)
def test_grupo_explicito_fora_do_split_e_recusado(raw: dict[str, object]) -> None:
    assert errors(raw) == [()]


@pytest.mark.parametrize(
    ("raw", "location"),
    [
        pytest.param({"operation": "rotate"}, ("operation",), id="operacao-desconhecida"),
        pytest.param(
            {"operation": "split", "pages_per_document": 0},
            ("pages_per_document",),
            id="grupo-zero",
        ),
        pytest.param(
            {"operation": "split", "pages_per_document": MAX_INPUT_PAGES + 1},
            ("pages_per_document",),
            id="grupo-acima",
        ),
        pytest.param(
            {"operation": "split", "pages_per_document": "2"},
            ("pages_per_document",),
            id="grupo-texto",
        ),
        pytest.param({"operation": "select"}, (), id="select-sem-lista"),
        pytest.param({"operation": "select", "select": []}, ("select",), id="select-vazio"),
        pytest.param(
            {"operation": "merge", "select": [{"upload_id": UPLOAD_A, "page_number": 1}]},
            (),
            id="lista-fora-do-select",
        ),
        pytest.param(selection((UPLOAD_A, 0)), ("select", "0", "page_number"), id="pagina-zero"),
        pytest.param(
            selection((UPLOAD_A, MAX_INPUT_PAGES + 1)),
            ("select", "0", "page_number"),
            id="pagina-acima",
        ),
        pytest.param(
            selection(("nao-e-uuid", 1)), ("select", "0", "upload_id"), id="upload-nao-uuid"
        ),
        pytest.param(
            {
                "operation": "select",
                "select": [{"upload_id": UPLOAD_A, "page_number": 1, "extra": 1}],
            },
            ("select", "0", "extra"),
            id="campo-extra-no-item",
        ),
        pytest.param({"pages": 2}, ("pages",), id="campo-desconhecido"),
    ],
)
def test_parametro_invalido_aponta_o_campo(
    raw: dict[str, object], location: tuple[str, ...]
) -> None:
    assert location in errors(raw)


def test_select_no_teto_de_paginas_de_saida_e_aceito() -> None:
    raw = selection(*[(UPLOAD_A, 1)] * MAX_OUTPUT_PAGES)

    assert len(PdfPressV1Parameters.model_validate(raw).select or []) == MAX_OUTPUT_PAGES


def test_select_acima_do_teto_de_paginas_de_saida_e_recusado() -> None:
    raw = selection(*[(UPLOAD_A, 1)] * (MAX_OUTPUT_PAGES + 1))

    assert errors(raw) == [("select",)]


def test_referencias_a_uploads_saem_dos_parametros_resolvidos() -> None:
    resolved = CATALOG.resolve("pdf-press", 1, selection((UPLOAD_B, 1), (UPLOAD_A, 2)))

    assert upload_references(resolved) == (
        UploadReference(("select", "0", "upload_id"), uuid.UUID(UPLOAD_B)),
        UploadReference(("select", "1", "upload_id"), uuid.UUID(UPLOAD_A)),
    )


def test_merge_nao_referencia_uploads() -> None:
    assert upload_references(CATALOG.resolve("pdf-press", 1, {})) == ()


def command(parameters: object, inputs: list[str]) -> RunCommand:
    return RunCommand("pdf-press", 1, tuple(uuid.UUID(i) for i in inputs), parameters)


def test_selecao_de_upload_fora_das_entradas_e_recusada_com_o_campo() -> None:
    with pytest.raises(InvalidRunCommandError) as caught:
        validate_run(command(selection((UPLOAD_A, 1), (UPLOAD_B, 1)), [UPLOAD_A]))

    assert [(e.location, e.message) for e in caught.value.errors] == [
        (
            ("parameters", "select", "1", "upload_id"),
            "o upload não está entre as entradas da execução",
        )
    ]


def test_selecao_de_uploads_das_entradas_passa() -> None:
    validated = validate_run(command(selection((UPLOAD_B, 1)), [UPLOAD_A, UPLOAD_B]))

    assert validated.parameters["operation"] == "select"


def test_validacao_sem_entradas_nao_confere_as_referencias() -> None:
    # validate_only não exige uploads (1.6); sem entradas não há com o que comparar.
    validated = validate_run(command(selection((UPLOAD_B, 1)), []))

    assert validated.parameters["operation"] == "select"


def test_entradas_acima_de_20_sao_recusadas() -> None:
    inputs = [str(uuid.uuid4()) for _ in range(21)]

    with pytest.raises(InvalidRunCommandError) as caught:
        validate_run(command({}, inputs))

    assert caught.value.errors[0].location == ("input_upload_ids",)


# --- v2: intervalos, rotação, compressão e strip_metadata (ADR 070) ----------------------------

RANGE = {"upload_id": UPLOAD_A, "first_page": 1, "last_page": 3, "rotation": 90}
RANGE_B = {"upload_id": UPLOAD_B, "first_page": 10, "last_page": None, "rotation": 0}


def _locations(raw: object) -> list[str]:
    with pytest.raises(InvalidParametersError) as caught:
        CATALOG.resolve("pdf-press", 2, raw)
    return [".".join(error.location) for error in caught.value.errors]


def test_pdf_press_v2_resolve_os_defaults() -> None:
    resolved = CATALOG.resolve("pdf-press", 2, {})

    assert resolved == {
        "compression": "none",
        "operation": "merge",
        "pages_per_document": 1,
        "rotation": 0,
        "select": None,
        "strip_metadata": True,
    }


def test_select_da_v2_resolvido_vira_os_params_da_biblioteca() -> None:
    raw = {
        "operation": "select",
        "compression": "lossless",
        "select": [{**RANGE, "upload_id": UPLOAD_A.upper()}, RANGE_B],
    }

    resolved = CATALOG.resolve("pdf-press", 2, raw)

    assert params_v2_from_mapping(resolved) == Params(
        Operation.SELECT,
        select=(PageRange(UPLOAD_A, 1, 3, 90), PageRange(UPLOAD_B, 10, None, 0)),
        compression=Compression.LOSSLESS,
        strip_metadata=True,
    )


def test_rotacao_do_intervalo_tem_default_zero() -> None:
    item = {"upload_id": UPLOAD_A, "first_page": 2, "last_page": 2}

    resolved = CATALOG.resolve("pdf-press", 2, {"operation": "select", "select": [item]})

    assert resolved["select"] == [{**item, "rotation": 0}]


def test_rotacao_explicita_no_select_e_recusada_no_campo() -> None:
    raw = {"operation": "select", "rotation": 0, "select": [RANGE]}

    with pytest.raises(InvalidParametersError) as caught:
        CATALOG.resolve("pdf-press", 2, raw)

    assert [error.location for error in caught.value.errors] == [()]
    assert "rotation só se aplica a merge e split" in str(caught.value)


@pytest.mark.parametrize("operation", ["merge", "split"])
def test_rotacao_global_vale_em_merge_e_split(operation: str) -> None:
    resolved = CATALOG.resolve("pdf-press", 2, {"operation": operation, "rotation": 270})

    assert resolved["rotation"] == 270


def test_last_page_e_obrigatorio_mesmo_nulo() -> None:
    raw = {"operation": "select", "select": [{"upload_id": UPLOAD_A, "first_page": 1}]}

    assert _locations(raw) == ["select.0.last_page"]


@pytest.mark.parametrize(
    ("raw", "location"),
    [
        pytest.param({"rotation": 45}, "rotation", id="rotacao-fora-dos-angulos"),
        pytest.param({"rotation": True}, "rotation", id="rotacao-booleana"),
        pytest.param({"compression": "lossy"}, "compression", id="compressao-desconhecida"),
        pytest.param({"strip_metadata": "sim"}, "strip_metadata", id="strip-texto"),
        pytest.param(
            {"operation": "select", "select": [{**RANGE, "first_page": 5, "last_page": 2}]},
            "select.0",
            id="intervalo-invertido",
        ),
        pytest.param(
            {"operation": "select", "select": [{**RANGE, "rotation": 45}]},
            "select.0.rotation",
            id="rotacao-do-intervalo",
        ),
        pytest.param(
            {"operation": "select", "select": [{**RANGE, "first_page": 0}]},
            "select.0.first_page",
            id="primeira-zero",
        ),
        pytest.param(
            {"operation": "select", "select": [{"upload_id": UPLOAD_A, "page_number": 1}]},
            "select.0.page_number",
            id="item-da-v1",
        ),
        pytest.param(
            {"operation": "select", "select": [RANGE] * (MAX_OUTPUT_PAGES + 1)},
            "select",
            id="intervalos-acima-do-teto",
        ),
    ],
)
def test_parametro_invalido_da_v2_aponta_o_campo(raw: dict[str, object], location: str) -> None:
    assert location in _locations(raw)


def test_v1_recusa_opcao_da_v2_no_campo() -> None:
    with pytest.raises(InvalidParametersError) as caught:
        CATALOG.resolve("pdf-press", 1, {"rotation": 90})

    assert [error.location for error in caught.value.errors] == [("rotation",)]


def test_upload_references_da_v2_cita_cada_intervalo() -> None:
    parameters = CATALOG.resolve(
        "pdf-press", 2, {"operation": "select", "select": [RANGE, RANGE_B]}
    )

    references = CATALOG.get_version("pdf-press", 2).upload_references(parameters)

    assert references == (
        UploadReference(("select", "0", "upload_id"), uuid.UUID(UPLOAD_A)),
        UploadReference(("select", "1", "upload_id"), uuid.UUID(UPLOAD_B)),
    )


def test_selecao_v2_de_upload_fora_das_entradas_e_recusada_com_o_campo() -> None:
    raw = {"operation": "select", "select": [RANGE, RANGE_B]}

    with pytest.raises(InvalidRunCommandError) as caught:
        validate_run(RunCommand("pdf-press", 2, (uuid.UUID(UPLOAD_A),), raw))

    assert [e.location for e in caught.value.errors] == [("parameters", "select", "1", "upload_id")]


def test_registro_da_v2_gira_e_comprime(tmp_path: Path) -> None:
    data = samples.document("a", pages=2, compress=False)
    (tmp_path / "out").mkdir()
    source = tmp_path / "a.pdf"
    source.write_bytes(data)
    entry = InputFile(0, source, "a.pdf", len(data), hashlib.sha256(data).hexdigest())
    resolved = CATALOG.resolve("pdf-press", 2, {"rotation": 90, "compression": "lossless"})
    out = FileSystemOutputDir(tmp_path / "out", reserved_names=PDF_PRESS_RESERVED_NAMES)

    report = RECIPES[("pdf-press", 2)].execute(resolved, [entry], out, RecordedProgress())

    assert report.details["compression"] == "lossless"
    with pikepdf.open(tmp_path / "out" / "merged.pdf") as pdf:
        assert [int(page.obj.Rotate) for page in pdf.pages] == [90, 90]


def test_v1_fica_deprecated_com_sunset_e_v2_e_a_atual() -> None:
    v1 = CATALOG.get_version("pdf-press", 1)
    v2 = CATALOG.get_version("pdf-press", 2)

    assert v1.status is VersionStatus.DEPRECATED
    assert v1.sunset_at == sunset_after_merge(PDF_PRESS_V2_MERGE_DEADLINE)
    assert v1.sunset_at == datetime(2027, 4, 1, tzinfo=UTC)
    assert v2.status is VersionStatus.CURRENT
    assert v2.limits == v1.limits
    assert v2.upload_references is v1.upload_references
