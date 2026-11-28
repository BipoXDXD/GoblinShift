"""Plano dos documentos de saída a partir dos parâmetros e das páginas de cada entrada (1.5)."""

import pytest

from goblinshift.processing.pdf_press import Limits, Operation, PageRange, Params
from goblinshift.processing.pdf_press.composition import (
    InputDocument,
    PlannedDocument,
    SourcePage,
    plan_documents,
)
from goblinshift.processing.recipe import FailureCode, InputContractError, RecipeFailure

UPLOAD_A = "4eb3e442-3f30-4d83-8d34-cb18a99a46cb"
UPLOAD_B = "1b8f283e-32ee-4687-9bd1-ae2f75f93cc2"
UPLOAD_C = "0f9d5a51-2b7e-4c3e-9b0c-1d2e3f405162"


def document(position: int, name: str, pages: int, upload_id: str | None = None) -> InputDocument:
    return InputDocument(position, name, upload_id, pages)


def pages(position: int, *numbers: int) -> tuple[SourcePage, ...]:
    return tuple(SourcePage(position, number) for number in numbers)


def failure_code(params: Params, inputs: list[InputDocument], limits: Limits) -> FailureCode:
    with pytest.raises(RecipeFailure) as caught:
        plan_documents(params, inputs, limits)
    return caught.value.code


def test_merge_junta_todas_as_paginas_na_ordem_das_entradas() -> None:
    inputs = [document(0, "a.pdf", 2), document(1, "b.pdf", 3)]

    plan = plan_documents(Params(), inputs, Limits())

    assert plan == (PlannedDocument("merged.pdf", 0, (*pages(0, 1, 2), *pages(1, 1, 2, 3))),)


def test_merge_de_uma_entrada_copia_as_paginas() -> None:
    plan = plan_documents(Params(), [document(0, "a.pdf", 1)], Limits())

    assert plan == (PlannedDocument("merged.pdf", 0, pages(0, 1)),)


def test_split_de_uma_em_uma_pagina_nomeia_pela_pagina() -> None:
    params = Params(operation=Operation.SPLIT)

    plan = plan_documents(params, [document(0, "Relatório final.pdf", 3)], Limits())

    assert plan == (
        PlannedDocument("Relatório final-p001.pdf", 0, pages(0, 1)),
        PlannedDocument("Relatório final-p002.pdf", 0, pages(0, 2)),
        PlannedDocument("Relatório final-p003.pdf", 0, pages(0, 3)),
    )


def test_split_em_grupos_deixa_o_resto_no_ultimo_documento() -> None:
    params = Params(operation=Operation.SPLIT, pages_per_document=2)

    plan = plan_documents(params, [document(0, "a.pdf", 5)], Limits())

    assert plan == (
        PlannedDocument("a-p001-002.pdf", 0, pages(0, 1, 2)),
        PlannedDocument("a-p003-004.pdf", 0, pages(0, 3, 4)),
        PlannedDocument("a-p005.pdf", 0, pages(0, 5)),
    )


def test_split_com_grupo_maior_que_o_documento_gera_um_so() -> None:
    params = Params(operation=Operation.SPLIT, pages_per_document=10)

    plan = plan_documents(params, [document(0, "a.pdf", 3)], Limits())

    assert plan == (PlannedDocument("a-p001-003.pdf", 0, pages(0, 1, 2, 3)),)


def test_split_de_varias_entradas_segue_a_ordem_das_entradas() -> None:
    params = Params(operation=Operation.SPLIT, pages_per_document=2)
    inputs = [document(0, "b.pdf", 2), document(1, "a.pdf", 1)]

    plan = plan_documents(params, inputs, Limits())

    assert plan == (
        PlannedDocument("b-p001-002.pdf", 0, pages(0, 1, 2)),
        PlannedDocument("a-p001.pdf", 1, pages(1, 1)),
    )


@pytest.mark.parametrize(
    ("original_name", "logical_name"),
    [
        pytest.param("../../etc/passwd", "_.-p001.pdf", id="traversal"),
        pytest.param("a/b.pdf", "a_b-p001.pdf", id="separador"),
        pytest.param("fatura\u202egpj.pdf", "fatura_gpj-p001.pdf", id="bidi"),
        pytest.param("..pdf", "documento-p001.pdf", id="so-pontos"),
        pytest.param("sem extensao", "sem extensao-p001.pdf", id="sem-extensao"),
    ],
)
def test_split_deriva_o_nome_seguro_do_original(original_name: str, logical_name: str) -> None:
    params = Params(operation=Operation.SPLIT, pages_per_document=1)

    plan = plan_documents(params, [document(0, original_name, 1)], Limits())

    assert plan[0].logical_name == logical_name


def test_select_respeita_ordem_e_repeticoes_e_atribui_a_primeira_pagina() -> None:
    params = Params(
        operation=Operation.SELECT,
        select=(
            PageRange.single(UPLOAD_B, 2),
            PageRange.single(UPLOAD_A, 1),
            PageRange.single(UPLOAD_B, 2),
        ),
    )
    inputs = [document(0, "a.pdf", 1, UPLOAD_A), document(1, "b.pdf", 2, UPLOAD_B)]

    plan = plan_documents(params, inputs, Limits())

    assert plan == (
        PlannedDocument("selection.pdf", 1, (SourcePage(1, 2), SourcePage(0, 1), SourcePage(1, 2))),
    )


def test_select_de_pagina_inexistente_falha_a_execucao() -> None:
    params = Params(operation=Operation.SELECT, select=(PageRange.single(UPLOAD_A, 3),))
    inputs = [document(0, "a.pdf", 2, UPLOAD_A)]

    assert failure_code(params, inputs, Limits()) is FailureCode.PDF_PAGE_NOT_FOUND


def test_select_da_ultima_pagina_existe() -> None:
    params = Params(operation=Operation.SELECT, select=(PageRange.single(UPLOAD_A, 2),))
    inputs = [document(0, "a.pdf", 2, UPLOAD_A)]

    assert plan_documents(params, inputs, Limits())[0].pages == pages(0, 2)


def test_select_de_upload_fora_das_entradas_e_defeito_de_infraestrutura() -> None:
    # A aceitação já recusa a referência (422); chegar aqui é job fora do contrato.
    params = Params(operation=Operation.SELECT, select=(PageRange.single(UPLOAD_C, 1),))
    inputs = [document(0, "a.pdf", 1, UPLOAD_A)]

    with pytest.raises(InputContractError, match="entradas"):
        plan_documents(params, inputs, Limits())


def test_select_sem_ids_dos_uploads_no_contexto_e_defeito_de_infraestrutura() -> None:
    params = Params(operation=Operation.SELECT, select=(PageRange.single(UPLOAD_A, 1),))

    with pytest.raises(InputContractError, match="upload"):
        plan_documents(params, [document(0, "a.pdf", 1, None)], Limits())


def test_select_acima_do_teto_de_paginas_de_saida_falha() -> None:
    params = Params(operation=Operation.SELECT, select=(PageRange.single(UPLOAD_A, 1),) * 3)
    inputs = [document(0, "a.pdf", 1, UPLOAD_A)]

    code = failure_code(params, inputs, Limits(max_output_pages=2))

    assert code is FailureCode.OUTPUT_LIMIT_EXCEEDED


def test_select_no_teto_de_paginas_de_saida_passa() -> None:
    params = Params(operation=Operation.SELECT, select=(PageRange.single(UPLOAD_A, 1),) * 2)
    inputs = [document(0, "a.pdf", 1, UPLOAD_A)]

    plan = plan_documents(params, inputs, Limits(max_output_pages=2))

    assert len(plan[0].pages) == 2


def test_split_acima_do_teto_de_documentos_falha() -> None:
    params = Params(operation=Operation.SPLIT)

    code = failure_code(params, [document(0, "a.pdf", 3)], Limits(max_output_documents=2))

    assert code is FailureCode.OUTPUT_LIMIT_EXCEEDED


def test_split_no_teto_de_documentos_passa() -> None:
    params = Params(operation=Operation.SPLIT)

    plan = plan_documents(params, [document(0, "a.pdf", 2)], Limits(max_output_documents=2))

    assert len(plan) == 2


def test_merge_acima_do_teto_de_paginas_de_saida_falha() -> None:
    inputs = [document(0, "a.pdf", 2), document(1, "b.pdf", 2)]

    code = failure_code(Params(), inputs, Limits(max_output_pages=3))

    assert code is FailureCode.OUTPUT_LIMIT_EXCEEDED


# --- v2: intervalos expandidos no executor e rotação por página (ADR 070) ----------------------


def failure(params: Params, inputs: list[InputDocument], limits: Limits) -> RecipeFailure:
    with pytest.raises(RecipeFailure) as caught:
        plan_documents(params, inputs, limits)
    return caught.value


def test_intervalos_expandem_na_ordem_com_rotacao() -> None:
    params = Params(
        Operation.SELECT, select=(PageRange(UPLOAD_A, 2, 3, 90), PageRange(UPLOAD_B, 1, 1))
    )
    inputs = [document(0, "a.pdf", 5, UPLOAD_A), document(1, "b.pdf", 2, UPLOAD_B)]

    plan = plan_documents(params, inputs, Limits())

    assert plan[0].pages == (SourcePage(0, 2, 90), SourcePage(0, 3, 90), SourcePage(1, 1, 0))


def test_intervalo_aberto_vai_ate_a_ultima_pagina() -> None:
    params = Params(Operation.SELECT, select=(PageRange(UPLOAD_A, 3, None),))

    plan = plan_documents(params, [document(0, "a.pdf", 5, UPLOAD_A)], Limits())

    assert [page.page_number for page in plan[0].pages] == [3, 4, 5]


def test_intervalo_aberto_na_ultima_pagina_tem_uma_pagina() -> None:
    params = Params(Operation.SELECT, select=(PageRange(UPLOAD_A, 10, None),))

    plan = plan_documents(params, [document(0, "a.pdf", 10, UPLOAD_A)], Limits())

    assert plan[0].pages == pages(0, 10)


def test_intervalo_aberto_alem_do_fim_falha() -> None:
    params = Params(Operation.SELECT, select=(PageRange(UPLOAD_A, 10, None),))

    caught = failure(params, [document(0, "a.pdf", 9, UPLOAD_A)], Limits())

    assert caught.code is FailureCode.PDF_PAGE_NOT_FOUND
    assert caught.detail == "a entrada 1 tem 9 páginas; a seleção pede a página 10"


def test_fim_do_intervalo_alem_do_documento_falha() -> None:
    params = Params(Operation.SELECT, select=(PageRange(UPLOAD_A, 2, 12),))

    caught = failure(params, [document(0, "a.pdf", 9, UPLOAD_A)], Limits())

    assert caught.code is FailureCode.PDF_PAGE_NOT_FOUND
    assert caught.detail == "a entrada 1 tem 9 páginas; a seleção pede a página 12"


def test_intervalos_que_somam_acima_do_teto_de_saida_falham() -> None:
    params = Params(Operation.SELECT, select=(PageRange(UPLOAD_A, 1, 200),) * 3)

    caught = failure(params, [document(0, "a.pdf", 200, UPLOAD_A)], Limits(max_output_pages=400))

    assert caught.code is FailureCode.OUTPUT_LIMIT_EXCEEDED
    assert "seriam 600 páginas" in caught.detail


@pytest.mark.parametrize("operation", [Operation.MERGE, Operation.SPLIT])
def test_rotacao_global_vale_em_merge_e_split(operation: Operation) -> None:
    inputs = [document(0, "a.pdf", 2), document(1, "b.pdf", 1)]

    plan = plan_documents(Params(operation, rotation=180), inputs, Limits())

    assert {page.rotation for planned in plan for page in planned.pages} == {180}
    assert sum(len(planned.pages) for planned in plan) == 3
