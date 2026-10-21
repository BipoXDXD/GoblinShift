"""Receita pdf-press v1 sobre o contrato de 3.2 (docs/GoblinShiftPlan.md, 1.5 e 1.7; ADR 035)."""

import json

import pikepdf
import pytest

from goblinshift.processing.output_dir import FileSystemOutputDir
from goblinshift.processing.pdf_press import Limits, Operation, PageSelection, Params
from goblinshift.processing.pdf_press.recipe import PdfPressV1
from goblinshift.processing.recipe import (
    FailureCode,
    InputContractError,
    ItemOk,
    Preview,
    RecipeFailure,
    ReportWarning,
)
from tests.pdf_press import samples
from tests.pdf_press.conftest import Workspace

UPLOAD_A = "4eb3e442-3f30-4d83-8d34-cb18a99a46cb"
UPLOAD_B = "1b8f283e-32ee-4687-9bd1-ae2f75f93cc2"
UPLOAD_C = "0f9d5a51-2b7e-4c3e-9b0c-1d2e3f405162"
NOT_SELECTED = ReportWarning("INPUT_NOT_SELECTED", "nenhuma página desta entrada foi selecionada")


def select(*pages: tuple[str, int]) -> Params:
    return Params(
        operation=Operation.SELECT,
        select=tuple(PageSelection(upload_id, number) for upload_id, number in pages),
    )


def test_merge_concatena_as_entradas_na_ordem(work: Workspace) -> None:
    work.add("a.pdf", samples.document("a", pages=2))
    work.add("b.pdf", samples.document("b", pages=1))

    report = work.run()

    assert report.items == [
        ItemOk(
            0,
            ("page-map.json", "merged.pdf"),
            counts={"pages": 2, "output_pages": 2},
            details={"pdf_version": "1.4"},
            previews=(Preview("previews/merged.json", "merged.pdf"),),
        ),
        ItemOk(1, (), counts={"pages": 1, "output_pages": 1}, details={"pdf_version": "1.4"}),
    ]
    assert samples.markers(work.read("merged.pdf")) == ["a:1", "a:2", "b:1"]
    assert report.details == {"operation": "merge", "documents": 1, "output_pages": 3}
    assert report.warnings == ()
    assert work.progress.done == [0, 1]


def test_split_separa_cada_entrada_em_grupos(work: Workspace) -> None:
    work.add("a.pdf", samples.document("a", pages=3))
    work.add("b.pdf", samples.document("b", pages=1))

    report = work.run(Params(operation=Operation.SPLIT, pages_per_document=2))

    assert [item.outputs for item in report.items if isinstance(item, ItemOk)] == [
        ("page-map.json", "a-p001-002.pdf", "a-p003.pdf"),
        ("b-p001.pdf",),
    ]
    assert samples.markers(work.read("a-p001-002.pdf")) == ["a:1", "a:2"]
    assert samples.markers(work.read("a-p003.pdf")) == ["a:3"]
    assert samples.markers(work.read("b-p001.pdf")) == ["b:1"]
    assert report.details == {"operation": "split", "documents": 3, "output_pages": 4}


def test_split_de_nomes_iguais_nao_sobrescreve(work: Workspace) -> None:
    work.add("a.pdf", samples.document("a"))
    work.add("a.pdf", samples.document("b"))

    work.run(Params(operation=Operation.SPLIT))

    assert work.output_paths() == [
        "a-p001.pdf",
        "a-p001_1.pdf",
        "page-map.json",
        "previews/a-p001.json",
        "previews/a-p001_1.json",
    ]
    assert samples.markers(work.read("a-p001_1.pdf")) == ["b:1"]


def test_select_preserva_ordem_e_repeticoes(work: Workspace) -> None:
    work.add("a.pdf", samples.document("a", pages=2), UPLOAD_A)
    work.add("b.pdf", samples.document("b", pages=3), UPLOAD_B)

    report = work.run(select((UPLOAD_B, 3), (UPLOAD_A, 1), (UPLOAD_B, 3), (UPLOAD_A, 2)))

    assert samples.markers(work.read("selection.pdf")) == ["b:3", "a:1", "b:3", "a:2"]
    # O documento é atribuído à entrada da primeira página; o mapa fica com a primeira entrada.
    assert [item.outputs for item in report.items if isinstance(item, ItemOk)] == [
        ("page-map.json",),
        ("selection.pdf",),
    ]
    assert [item.counts for item in report.items if isinstance(item, ItemOk)] == [
        {"pages": 2, "output_pages": 2},
        {"pages": 3, "output_pages": 2},
    ]


def test_select_avisa_a_entrada_que_nao_contribuiu(work: Workspace) -> None:
    work.add("a.pdf", samples.document("a"), UPLOAD_A)
    work.add("b.pdf", samples.document("b"), UPLOAD_B)

    report = work.run(select((UPLOAD_A, 1)))

    assert report.items[1] == ItemOk(
        1,
        (),
        warnings=(NOT_SELECTED,),
        counts={"pages": 1, "output_pages": 0},
        details={"pdf_version": "1.4"},
    )


def test_mapa_de_paginas_descreve_entradas_e_documentos(work: Workspace) -> None:
    work.add("a.pdf", samples.document("a", pages=2), UPLOAD_A)
    work.add("b.pdf", samples.document("b"), UPLOAD_B)

    work.run(select((UPLOAD_B, 1), (UPLOAD_A, 2), (UPLOAD_B, 1)))

    assert work.page_map() == {
        "schema_version": 1,
        "operation": "select",
        "inputs": [
            {"position": 0, "original_name": "a.pdf", "pages": 2},
            {"position": 1, "original_name": "b.pdf", "pages": 1},
        ],
        "documents": [
            {
                "path": "selection.pdf",
                "pages": [
                    {"input_position": 1, "page_number": 1},
                    {"input_position": 0, "page_number": 2},
                    {"input_position": 1, "page_number": 1},
                ],
            }
        ],
    }


def test_saida_usa_a_maior_versao_das_entradas(work: Workspace) -> None:
    work.add("a.pdf", samples.document("a", version="1.4"))
    work.add("b.pdf", samples.document("b", version="1.7"))

    work.run()

    assert work.read("merged.pdf").startswith(b"%PDF-1.7")


def test_mesmas_entradas_geram_os_mesmos_bytes(
    work: Workspace, tmp_path_factory: pytest.TempPathFactory
) -> None:
    other = Workspace(tmp_path_factory.mktemp("outra"))
    for workspace in (work, other):
        workspace.add("a.pdf", samples.document("a", pages=2))
        workspace.add("b.pdf", samples.document("b"))
        workspace.run()

    assert work.read("merged.pdf") == other.read("merged.pdf")
    assert work.read("page-map.json") == other.read("page-map.json")


def test_saida_nao_carrega_pagina_nao_selecionada_por_link(work: Workspace) -> None:
    # Um link para a página 3 não pode arrastar a página 3 para dentro de um PDF que só tem a 1.
    def link_to_last_page(pdf: pikepdf.Pdf) -> None:
        destination = pikepdf.Array([pdf.pages[2].obj, pikepdf.Name.Fit])
        samples.link_annotation(pikepdf.Dictionary(S=pikepdf.Name.GoTo, D=destination))(pdf)

    work.add(
        "a.pdf", samples.document("a", pages=3, compress=False, mutate=link_to_last_page), UPLOAD_A
    )

    work.run(select((UPLOAD_A, 1)))

    assert b"gs:a:3" not in work.read("selection.pdf")
    with pikepdf.open(work.out_dir / "selection.pdf") as pdf:
        assert len(pdf.pages) == 1


def test_entrada_maliciosa_falha_a_execucao_sem_publicar_nada(work: Workspace) -> None:
    work.add("a.pdf", samples.document("a"))
    work.add("b.pdf", samples.document("b", mutate=samples.javascript_name_tree))

    with pytest.raises(RecipeFailure) as caught:
        work.run()

    assert caught.value.code is FailureCode.PDF_UNSUPPORTED_FEATURE
    assert "entrada 2" in caught.value.detail
    assert work.output_paths() == []


def test_pagina_inexistente_falha_sem_escrever_nada(work: Workspace) -> None:
    work.add("a.pdf", samples.document("a", pages=2), UPLOAD_A)

    with pytest.raises(RecipeFailure) as caught:
        work.run(select((UPLOAD_A, 1), (UPLOAD_A, 3)))

    assert caught.value.code is FailureCode.PDF_PAGE_NOT_FOUND
    assert work.output_paths() == []


def test_paginas_de_entrada_somadas_acima_do_teto_falham(work: Workspace) -> None:
    work.add("a.pdf", samples.document("a", pages=2))
    work.add("b.pdf", samples.document("b", pages=2))

    with pytest.raises(RecipeFailure) as caught:
        work.run(recipe=PdfPressV1(limits=Limits(max_input_pages=3)))

    assert caught.value.code is FailureCode.INPUT_LIMIT_EXCEEDED
    assert "entrada 2" in caught.value.detail


def test_paginas_de_entrada_no_teto_passam(work: Workspace) -> None:
    work.add("a.pdf", samples.document("a", pages=2))
    work.add("b.pdf", samples.document("b", pages=1))

    report = work.run(recipe=PdfPressV1(limits=Limits(max_input_pages=3)))

    assert report.details["output_pages"] == 3


def test_saidas_acima_do_teto_de_bytes_falham(work: Workspace) -> None:
    work.add("a.pdf", samples.document("a", pages=3))
    out = FileSystemOutputDir(work.out_dir, max_bytes=100)

    with pytest.raises(RecipeFailure) as caught:
        PdfPressV1().run(Params(), work.inputs, out, work.progress)

    assert caught.value.code is FailureCode.OUTPUT_LIMIT_EXCEEDED


def test_entradas_acima_do_teto_da_versao_sao_job_fora_do_contrato(work: Workspace) -> None:
    for position in range(3):
        work.add(f"{position}.pdf", samples.document(str(position)))

    with pytest.raises(InputContractError, match="2"):
        work.run(recipe=PdfPressV1(max_inputs=2))


def test_select_sem_upload_id_das_entradas_e_job_fora_do_contrato(work: Workspace) -> None:
    work.add("a.pdf", samples.document("a"))

    with pytest.raises(InputContractError):
        work.run(select((UPLOAD_C, 1)))


# --- Preview: o mapa de páginas de cada documento, sem renderizar PDF (2.2; ADR 037) -----------


def test_cada_documento_tem_o_proprio_mapa_de_paginas_como_preview(work: Workspace) -> None:
    work.add("a.pdf", samples.document("a", pages=3))

    report = work.run(Params(operation=Operation.SPLIT, pages_per_document=2))

    [item] = report.items
    assert isinstance(item, ItemOk)
    assert item.previews == (
        Preview("previews/a-p001-002.json", "a-p001-002.pdf"),
        Preview("previews/a-p003.json", "a-p003.pdf"),
    )
    assert json.loads(work.read("previews/a-p003.json")) == {
        "schema_version": 1,
        "path": "a-p003.pdf",
        "pages": [{"input_position": 0, "page_number": 3}],
    }


def test_preview_do_select_segue_a_ordem_e_as_repeticoes(work: Workspace) -> None:
    work.add("a.pdf", samples.document("a", pages=2), upload_id=UPLOAD_A)
    work.add("b.pdf", samples.document("b", pages=1), upload_id=UPLOAD_B)

    work.run(select((UPLOAD_B, 1), (UPLOAD_A, 2), (UPLOAD_B, 1)))

    assert json.loads(work.read("previews/selection.json"))["pages"] == [
        {"input_position": 1, "page_number": 1},
        {"input_position": 0, "page_number": 2},
        {"input_position": 1, "page_number": 1},
    ]
