"""Parâmetros do PDF Press (docs/GoblinShiftPlan.md, 1.5; ADR 035)."""

import pytest

from goblinshift.processing.pdf_press import (
    MAX_INPUT_PAGES,
    MAX_OUTPUT_PAGES,
    InvalidParametersError,
    Operation,
    PageSelection,
    Params,
    params_from_mapping,
)

UPLOAD_A = "4eb3e442-3f30-4d83-8d34-cb18a99a46cb"
UPLOAD_B = "1b8f283e-32ee-4687-9bd1-ae2f75f93cc2"


def test_defaults_juntam_os_documentos_na_ordem() -> None:
    assert Params() == Params(operation=Operation.MERGE, pages_per_document=1, select=None)


@pytest.mark.parametrize("size", [1, 2, MAX_INPUT_PAGES])
def test_split_aceita_grupos_de_1_ate_o_teto_de_paginas(size: int) -> None:
    assert Params(operation=Operation.SPLIT, pages_per_document=size).pages_per_document == size


@pytest.mark.parametrize("size", [0, -1, MAX_INPUT_PAGES + 1])
def test_split_recusa_grupo_fora_da_faixa(size: int) -> None:
    with pytest.raises(InvalidParametersError, match="pages_per_document"):
        Params(operation=Operation.SPLIT, pages_per_document=size)


def test_select_guarda_a_lista_na_ordem_com_repeticoes() -> None:
    pages = (
        PageSelection(UPLOAD_B, 2),
        PageSelection(UPLOAD_A, 1),
        PageSelection(UPLOAD_B, 2),
    )

    assert Params(operation=Operation.SELECT, select=pages).select == pages


def test_select_sem_lista_e_recusado() -> None:
    with pytest.raises(InvalidParametersError, match="select"):
        Params(operation=Operation.SELECT)


def test_select_com_lista_vazia_e_recusado() -> None:
    with pytest.raises(InvalidParametersError, match="select"):
        Params(operation=Operation.SELECT, select=())


def test_select_acima_do_teto_de_paginas_de_saida_e_recusado() -> None:
    pages = (PageSelection(UPLOAD_A, 1),) * (MAX_OUTPUT_PAGES + 1)

    with pytest.raises(InvalidParametersError, match=str(MAX_OUTPUT_PAGES)):
        Params(operation=Operation.SELECT, select=pages)


def test_select_no_teto_de_paginas_de_saida_e_aceito() -> None:
    pages = (PageSelection(UPLOAD_A, 1),) * MAX_OUTPUT_PAGES

    assert len(Params(operation=Operation.SELECT, select=pages).select or ()) == MAX_OUTPUT_PAGES


@pytest.mark.parametrize("operation", [Operation.MERGE, Operation.SPLIT])
def test_lista_de_paginas_fora_do_select_e_recusada(operation: Operation) -> None:
    with pytest.raises(InvalidParametersError, match="select"):
        Params(operation=operation, select=(PageSelection(UPLOAD_A, 1),))


@pytest.mark.parametrize("page", [0, -3, MAX_INPUT_PAGES + 1])
def test_pagina_fora_da_faixa_e_recusada(page: int) -> None:
    with pytest.raises(InvalidParametersError, match="page_number"):
        PageSelection(UPLOAD_A, page)


@pytest.mark.parametrize("page", [1, MAX_INPUT_PAGES])
def test_pagina_na_fronteira_e_aceita(page: int) -> None:
    assert PageSelection(UPLOAD_A, page).page_number == page


@pytest.mark.parametrize(
    "upload_id",
    [
        pytest.param("", id="vazio"),
        pytest.param("nao-e-uuid", id="texto"),
        pytest.param(UPLOAD_A.upper(), id="maiusculas"),
        pytest.param(UPLOAD_A.replace("-", ""), id="sem-hifens"),
        pytest.param("{" + UPLOAD_A + "}", id="chaves"),
    ],
)
def test_upload_id_fora_da_forma_canonica_e_recusado(upload_id: str) -> None:
    # O catálogo normaliza o UUID; a forma canônica é a única que chega ao executor.
    with pytest.raises(InvalidParametersError, match="upload_id"):
        PageSelection(upload_id, 1)


def test_mapeamento_resolvido_do_merge_vira_params() -> None:
    raw = {"operation": "merge", "pages_per_document": 1, "select": None}

    assert params_from_mapping(raw) == Params()


def test_mapeamento_resolvido_do_select_vira_params() -> None:
    raw = {
        "operation": "select",
        "pages_per_document": 1,
        "select": [
            {"upload_id": UPLOAD_A, "page_number": 3},
            {"upload_id": UPLOAD_A, "page_number": 3},
        ],
    }

    assert params_from_mapping(raw) == Params(
        operation=Operation.SELECT,
        select=(PageSelection(UPLOAD_A, 3), PageSelection(UPLOAD_A, 3)),
    )


def test_mapeamento_vazio_usa_os_defaults() -> None:
    assert params_from_mapping({}) == Params()


def test_campo_desconhecido_e_recusado() -> None:
    with pytest.raises(InvalidParametersError, match="desconhecid"):
        params_from_mapping({"pages": 2})


@pytest.mark.parametrize(
    ("raw", "option"),
    [
        pytest.param({"operation": "rotate"}, "operation", id="operacao-desconhecida"),
        pytest.param({"operation": 1}, "operation", id="operacao-numero"),
        pytest.param({"pages_per_document": "2"}, "pages_per_document", id="grupo-texto"),
        pytest.param({"pages_per_document": True}, "pages_per_document", id="grupo-booleano"),
        pytest.param({"pages_per_document": 2.0}, "pages_per_document", id="grupo-float"),
        pytest.param({"operation": "select", "select": {}}, "select", id="select-objeto"),
        pytest.param({"operation": "select", "select": [1]}, "select", id="item-numero"),
        pytest.param(
            {"operation": "select", "select": [{"upload_id": UPLOAD_A}]},
            "select",
            id="item-sem-pagina",
        ),
        pytest.param(
            {"operation": "select", "select": [{"upload_id": UPLOAD_A, "page_number": 1, "x": 1}]},
            "select",
            id="item-com-campo-extra",
        ),
        pytest.param(
            {"operation": "select", "select": [{"upload_id": UPLOAD_A, "page_number": "1"}]},
            "page_number",
            id="pagina-texto",
        ),
        pytest.param(
            {"operation": "select", "select": [{"upload_id": 7, "page_number": 1}]},
            "upload_id",
            id="upload-numero",
        ),
    ],
)
def test_tipo_errado_e_recusado(raw: dict[str, object], option: str) -> None:
    with pytest.raises(InvalidParametersError, match=option):
        params_from_mapping(raw)
