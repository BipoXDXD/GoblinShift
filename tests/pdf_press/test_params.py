"""Parâmetros do PDF Press (docs/GoblinShiftPlan.md, 1.5; ADR 035)."""

import pytest

from goblinshift.processing.pdf_press import (
    MAX_INPUT_PAGES,
    MAX_OUTPUT_PAGES,
    Compression,
    InvalidParametersError,
    Operation,
    PageRange,
    Params,
    params_from_mapping,
    params_v2_from_mapping,
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
        PageRange.single(UPLOAD_B, 2),
        PageRange.single(UPLOAD_A, 1),
        PageRange.single(UPLOAD_B, 2),
    )

    assert Params(operation=Operation.SELECT, select=pages).select == pages


def test_select_sem_lista_e_recusado() -> None:
    with pytest.raises(InvalidParametersError, match="select"):
        Params(operation=Operation.SELECT)


def test_select_com_lista_vazia_e_recusado() -> None:
    with pytest.raises(InvalidParametersError, match="select"):
        Params(operation=Operation.SELECT, select=())


def test_select_acima_do_teto_de_paginas_de_saida_e_recusado() -> None:
    pages = (PageRange.single(UPLOAD_A, 1),) * (MAX_OUTPUT_PAGES + 1)

    with pytest.raises(InvalidParametersError, match=str(MAX_OUTPUT_PAGES)):
        Params(operation=Operation.SELECT, select=pages)


def test_select_no_teto_de_paginas_de_saida_e_aceito() -> None:
    pages = (PageRange.single(UPLOAD_A, 1),) * MAX_OUTPUT_PAGES

    assert len(Params(operation=Operation.SELECT, select=pages).select or ()) == MAX_OUTPUT_PAGES


@pytest.mark.parametrize("operation", [Operation.MERGE, Operation.SPLIT])
def test_lista_de_paginas_fora_do_select_e_recusada(operation: Operation) -> None:
    with pytest.raises(InvalidParametersError, match="select"):
        Params(operation=operation, select=(PageRange.single(UPLOAD_A, 1),))


@pytest.mark.parametrize("page", [0, -3, MAX_INPUT_PAGES + 1])
def test_pagina_fora_da_faixa_e_recusada(page: int) -> None:
    with pytest.raises(InvalidParametersError, match="first_page"):
        PageRange.single(UPLOAD_A, page)


@pytest.mark.parametrize("page", [1, MAX_INPUT_PAGES])
def test_pagina_na_fronteira_e_aceita(page: int) -> None:
    assert PageRange.single(UPLOAD_A, page) == PageRange(UPLOAD_A, page, page, 0)


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
        PageRange.single(upload_id, 1)


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
        select=(PageRange.single(UPLOAD_A, 3), PageRange.single(UPLOAD_A, 3)),
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


@pytest.mark.parametrize(
    ("item", "message"),
    [
        # Como antes da v2: upload_id é conferido antes de page_number.
        pytest.param({"upload_id": 7, "page_number": "1"}, "upload_id deve ser texto", id="tipos"),
        pytest.param(
            {"upload_id": "nao-e-uuid", "page_number": 0},
            "upload_id deve ser um UUID na forma canônica",
            id="uuid-e-pagina",
        ),
    ],
)
def test_parser_da_v1_confere_upload_id_antes_de_page_number(
    item: dict[str, object], message: str
) -> None:
    with pytest.raises(InvalidParametersError, match=f"^{message}$"):
        params_from_mapping({"operation": "select", "select": [item]})


@pytest.mark.parametrize(
    ("raw", "option"),
    [
        pytest.param(
            {"operation": "select", "select": [{"upload_id": UPLOAD_A, "page_number": 0}]},
            "page_number",
            id="pagina-zero",
        ),
    ],
)
def test_parser_da_v1_mantem_a_mensagem_de_page_number(raw: dict[str, object], option: str) -> None:
    with pytest.raises(InvalidParametersError, match=option):
        params_from_mapping(raw)


# --- v2: intervalos, rotação, compressão e strip_metadata (ADR 070) ----------------------------


def test_intervalo_aberto_e_fechado_sao_validos() -> None:
    assert PageRange(UPLOAD_A, 10, None).last_page is None
    assert PageRange(UPLOAD_A, 1, 3, 90).rotation == 90


@pytest.mark.parametrize(
    ("first", "last", "rotation", "message"),
    [
        (0, 3, 0, "first_page"),
        (4, 3, 0, "last_page"),
        (1, MAX_INPUT_PAGES + 1, 0, "last_page"),
        (1, 1, 45, "rotation"),
    ],
)
def test_intervalo_invalido_e_recusado(first: int, last: int, rotation: int, message: str) -> None:
    with pytest.raises(InvalidParametersError, match=message):
        PageRange(UPLOAD_A, first, last, rotation)


def test_intervalo_com_upload_fora_da_forma_canonica_e_recusado() -> None:
    with pytest.raises(InvalidParametersError, match="upload_id"):
        PageRange(UPLOAD_A.upper(), 1, None)


def test_rotacao_global_no_select_e_recusada() -> None:
    with pytest.raises(InvalidParametersError, match="rotation não se aplica ao select"):
        Params(Operation.SELECT, select=(PageRange.single(UPLOAD_A, 1),), rotation=90)


@pytest.mark.parametrize("operation", [Operation.MERGE, Operation.SPLIT])
def test_rotacao_global_vale_em_merge_e_split(operation: Operation) -> None:
    assert Params(operation, rotation=270).rotation == 270


@pytest.mark.parametrize("rotation", [-90, 45, 360])
def test_rotacao_global_fora_dos_angulos_e_recusada(rotation: int) -> None:
    with pytest.raises(InvalidParametersError, match="rotation"):
        Params(rotation=rotation)


def test_defaults_da_biblioteca_sao_o_comportamento_da_v1() -> None:
    params = Params()

    assert (params.rotation, params.compression, params.strip_metadata) == (
        0,
        Compression.NONE,
        False,
    )


def test_parser_da_v1_continua_com_paginas_avulsas() -> None:
    params = params_from_mapping(
        {"operation": "select", "select": [{"upload_id": UPLOAD_A, "page_number": 2}]}
    )

    assert params.select == (PageRange(UPLOAD_A, 2, 2),)


@pytest.mark.parametrize("option", ["rotation", "compression", "strip_metadata"])
def test_parser_da_v1_recusa_opcoes_da_v2(option: str) -> None:
    with pytest.raises(InvalidParametersError, match="opções desconhecidas"):
        params_from_mapping({option: 0})


def test_parser_da_v2_le_intervalos_rotacao_e_compressao() -> None:
    raw = {
        "operation": "select",
        "pages_per_document": 1,
        "rotation": 0,
        "compression": "lossless",
        "strip_metadata": True,
        "select": [{"upload_id": UPLOAD_A, "first_page": 10, "last_page": None, "rotation": 270}],
    }

    assert params_v2_from_mapping(raw) == Params(
        Operation.SELECT, 1, (PageRange(UPLOAD_A, 10, None, 270),), 0, Compression.LOSSLESS, True
    )


def test_parser_da_v2_le_merge_girado() -> None:
    raw = {"operation": "merge", "rotation": 180, "compression": "none", "strip_metadata": False}

    assert params_v2_from_mapping(raw) == Params(rotation=180)


def _range(**overrides: object) -> dict[str, object]:
    return {"upload_id": UPLOAD_A, "first_page": 1, "last_page": 2, "rotation": 0, **overrides}


@pytest.mark.parametrize(
    ("raw", "option"),
    [
        pytest.param({"page_number": 1}, "desconhecidas", id="chave-da-v1"),
        pytest.param({"rotation": True}, "rotation", id="rotacao-booleana"),
        pytest.param({"rotation": "90"}, "rotation", id="rotacao-texto"),
        pytest.param({"compression": "lossy"}, "compression", id="compressao-desconhecida"),
        pytest.param({"compression": 1}, "compression", id="compressao-numero"),
        pytest.param({"strip_metadata": 1}, "strip_metadata", id="strip-numero"),
        pytest.param(
            {"operation": "select", "select": [_range(first_page=True)]},
            "first_page deve ser inteiro",
            id="primeira-booleana",
        ),
        pytest.param(
            {"operation": "select", "select": [_range(last_page="3")]},
            "last_page",
            id="ultima-texto",
        ),
        pytest.param(
            {"operation": "select", "select": [_range(rotation=False)]},
            "rotation",
            id="rotacao-do-intervalo-booleana",
        ),
        pytest.param(
            {"operation": "select", "select": [{"upload_id": UPLOAD_A, "first_page": 1}]},
            "exatamente",
            id="intervalo-sem-last-page",
        ),
        pytest.param(
            {"operation": "select", "select": [{"upload_id": UPLOAD_A, "page_number": 1}]},
            "exatamente",
            id="item-da-v1",
        ),
        pytest.param(
            {"operation": "select", "select": [_range(upload_id=7)]},
            "upload_id",
            id="upload-numero",
        ),
    ],
)
def test_parser_da_v2_recusa_tipo_errado(raw: dict[str, object], option: str) -> None:
    with pytest.raises(InvalidParametersError, match=option):
        params_v2_from_mapping(raw)
