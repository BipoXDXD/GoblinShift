"""Modelo dos parâmetros do pdf-press v1 e conferência das referências a uploads (1.5, 1.6)."""

import uuid

import pytest
from pydantic import ValidationError

from goblinshift.catalog import CATALOG
from goblinshift.catalog.pdf_press_v1 import PdfPressV1Parameters, upload_references
from goblinshift.catalog.references import UploadReference
from goblinshift.processing.pdf_press import (
    MAX_INPUT_PAGES,
    MAX_OUTPUT_PAGES,
    Operation,
    PageSelection,
    Params,
    params_from_mapping,
)
from goblinshift.runs.acceptance import InvalidRunCommandError, RunCommand, validate_run

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
        "max_total_input_bytes": 256 * 1024 * 1024,
        "max_input_pages": 200,
        "max_output_pages": 400,
        "max_output_documents": 200,
    }


def test_select_resolvido_normaliza_o_uuid_e_vira_os_params_da_biblioteca() -> None:
    resolved = CATALOG.resolve("pdf-press", 1, selection((UPLOAD_A.upper(), 2), (UPLOAD_A, 2)))

    assert params_from_mapping(resolved) == Params(
        operation=Operation.SELECT,
        select=(PageSelection(UPLOAD_A, 2), PageSelection(UPLOAD_A, 2)),
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
        UploadReference("select.0.upload_id", uuid.UUID(UPLOAD_B)),
        UploadReference("select.1.upload_id", uuid.UUID(UPLOAD_A)),
    )


def test_merge_nao_referencia_uploads() -> None:
    assert upload_references(CATALOG.resolve("pdf-press", 1, {})) == ()


def command(parameters: object, inputs: list[str]) -> RunCommand:
    return RunCommand("pdf-press", 1, tuple(uuid.UUID(i) for i in inputs), parameters)


def test_selecao_de_upload_fora_das_entradas_e_recusada_com_o_campo() -> None:
    with pytest.raises(InvalidRunCommandError) as caught:
        validate_run(command(selection((UPLOAD_A, 1), (UPLOAD_B, 1)), [UPLOAD_A]))

    assert [(e.location, e.message) for e in caught.value.errors] == [
        ("parameters.select.1.upload_id", "o upload não está entre as entradas da execução")
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

    assert caught.value.errors[0].location == "input_upload_ids"
