"""Receita pdf-press sobre o contrato de 3.2 (docs/GoblinShiftPlan.md, 1.5 e 1.7; ADR 035)."""

import hashlib
import io
import json
import zlib

import pikepdf
import pytest

from goblinshift.processing.output_dir import FileSystemOutputDir
from goblinshift.processing.pdf_press import (
    RECIPE_ID,
    Compression,
    Limits,
    Operation,
    PageRange,
    Params,
)
from goblinshift.processing.pdf_press.recipe import PdfPress
from goblinshift.processing.recipe import (
    FailureCode,
    InputContractError,
    ItemOk,
    Preview,
    RecipeFailure,
    ReportWarning,
)
from goblinshift.runs.comparison import PdfComparison, comparison_from_report
from tests.pdf_press import samples
from tests.pdf_press.conftest import Workspace

UPLOAD_A = "4eb3e442-3f30-4d83-8d34-cb18a99a46cb"
UPLOAD_B = "1b8f283e-32ee-4687-9bd1-ae2f75f93cc2"
UPLOAD_C = "0f9d5a51-2b7e-4c3e-9b0c-1d2e3f405162"
NOT_SELECTED = ReportWarning("INPUT_NOT_SELECTED", "nenhuma página desta entrada foi selecionada")


def select(*pages: tuple[str, int]) -> Params:
    return Params(
        operation=Operation.SELECT,
        select=tuple(PageRange.single(upload_id, number) for upload_id, number in pages),
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


def test_contagens_do_item_viram_comparacao(work: Workspace) -> None:
    """Chave renomeada na receita zeraria a comparação em silêncio."""
    work.add("a.pdf", samples.document("a", pages=3), UPLOAD_A)

    item = work.run(select((UPLOAD_A, 2))).items[0]

    assert isinstance(item, ItemOk)
    assert comparison_from_report(RECIPE_ID, item.counts, item.details) == PdfComparison(3, 1)


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


def private_keys(data: bytes) -> list[str]:
    """Chaves privadas em todo dicionário alcançável do trailer, direto ou indireto."""
    found: list[str] = []
    with pikepdf.open(io.BytesIO(data)) as pdf:
        seen: set[tuple[int, int]] = set()
        stack: list[object] = [pdf.trailer]
        while stack:
            node = stack.pop()
            if not isinstance(node, pikepdf.Array | pikepdf.Dictionary | pikepdf.Stream):
                continue
            if node.is_indirect:
                if node.objgen in seen:
                    continue
                seen.add(node.objgen)
            if isinstance(node, pikepdf.Array):
                stack.extend(node)
            else:
                found += [k for k in ("/Metadata", "/PieceInfo", "/LastModified") if k in node]
                stack.extend(node.values())
    return found


def xml_streams(data: bytes) -> int:
    with pikepdf.open(io.BytesIO(data)) as pdf:
        return sum(
            isinstance(o, pikepdf.Stream) and o.get("/Subtype") == pikepdf.Name.XML
            for o in pdf.objects
        )


def test_saida_sai_sem_metadados_privados_de_pagina_e_xobject(work: Workspace) -> None:
    work.add("a.pdf", samples.document("a", mutate=samples.private_metadata))

    work.run()

    merged = work.read("merged.pdf")
    assert private_keys(merged) == []
    assert xml_streams(merged) == 0
    assert samples.markers(merged) == ["a:1"]
    with pikepdf.open(io.BytesIO(merged)) as pdf:
        assert list(pdf.pages[0].Resources.XObject.keys()) == ["/Fm0"]


def test_split_tira_os_metadados_de_cada_parte(work: Workspace) -> None:
    work.add("a.pdf", samples.document("a", pages=2, mutate=samples.private_metadata))

    work.run(Params(operation=Operation.SPLIT, pages_per_document=1))

    for name in ("a-p001.pdf", "a-p002.pdf"):
        assert private_keys(work.read(name)) == []
        assert xml_streams(work.read(name)) == 0


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


def test_mapa_de_paginas_registra_a_rotacao_so_quando_ha(work: Workspace) -> None:
    work.add("a.pdf", samples.document("a", pages=2), UPLOAD_A)

    work.run(
        Params(
            Operation.SELECT,
            select=(PageRange(UPLOAD_A, 1, 1, 90), PageRange(UPLOAD_A, 2, 2)),
        )
    )

    expected = [
        {"input_position": 0, "page_number": 1, "rotation": 90},
        {"input_position": 0, "page_number": 2},
    ]
    assert work.page_map()["documents"][0]["pages"] == expected  # type: ignore[index]
    assert json.loads(work.read("previews/selection.json"))["pages"] == expected


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
        work.run(recipe=PdfPress(limits=Limits(max_input_pages=3)))

    assert caught.value.code is FailureCode.INPUT_LIMIT_EXCEEDED
    assert "entrada 2" in caught.value.detail


def test_paginas_de_entrada_no_teto_passam(work: Workspace) -> None:
    work.add("a.pdf", samples.document("a", pages=2))
    work.add("b.pdf", samples.document("b", pages=1))

    report = work.run(recipe=PdfPress(limits=Limits(max_input_pages=3)))

    assert report.details["output_pages"] == 3


def test_saidas_acima_do_teto_de_bytes_falham(work: Workspace) -> None:
    work.add("a.pdf", samples.document("a", pages=3))
    out = FileSystemOutputDir(work.out_dir, max_bytes=100)

    with pytest.raises(RecipeFailure) as caught:
        PdfPress().run(Params(), work.inputs, out, work.progress)

    assert caught.value.code is FailureCode.OUTPUT_LIMIT_EXCEEDED


def test_entradas_acima_do_teto_da_versao_sao_job_fora_do_contrato(work: Workspace) -> None:
    for position in range(3):
        work.add(f"{position}.pdf", samples.document(str(position)))

    with pytest.raises(InputContractError, match="2"):
        work.run(recipe=PdfPress(max_inputs=2))


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


# --- v2: rotação relativa e anotações sem autor nem datas (ADR 070) ----------------------------

# Saída do merge v1 de document("a", 2) + document("b", 1), calculada com o código da main antes
# da rotação e do strip_metadata: a v1 tem de continuar gerando exatamente estes bytes.
V1_SAMPLE_SHA256 = "bc4336b6e64f69abfb934eb02c9e050ee427fc294e360014741dc4018cbd8ff6"
ANNOTATION_METADATA = ("/T", "/M", "/CreationDate")


def rotations(data: bytes) -> list[int | None]:
    with pikepdf.open(io.BytesIO(data)) as pdf:
        return [None if "/Rotate" not in page.obj else int(page.obj.Rotate) for page in pdf.pages]


def annotation_keys(data: bytes) -> list[set[str]]:
    """As chaves de cada anotação da primeira página, na ordem de `/Annots`."""
    with pikepdf.open(io.BytesIO(data)) as pdf:
        return [set(annotation.keys()) for annotation in pdf.pages[0].obj.Annots]


def test_v1_continua_gerando_os_mesmos_bytes(work: Workspace) -> None:
    work.add("a.pdf", samples.document("a", pages=2))
    work.add("b.pdf", samples.document("b", pages=1))

    work.run()

    assert hashlib.sha256(work.read("merged.pdf")).hexdigest() == V1_SAMPLE_SHA256


def test_rotacao_gira_so_as_paginas_pedidas(work: Workspace) -> None:
    work.add("a.pdf", samples.document("a", pages=2), UPLOAD_A)

    work.run(
        Params(Operation.SELECT, select=(PageRange(UPLOAD_A, 1, 1, 90), PageRange(UPLOAD_A, 2, 2)))
    )

    assert rotations(work.read("selection.pdf")) == [90, None]


def test_rotacao_soma_a_rotacao_herdada(work: Workspace) -> None:
    work.add("a.pdf", samples.document("a", pages=2, mutate=samples.inherited_rotation(90)))

    work.run(Params(rotation=90))

    assert rotations(work.read("merged.pdf")) == [180, 180]


def test_rotacao_de_270_sobre_90_volta_a_zero(work: Workspace) -> None:
    work.add("a.pdf", samples.document("a", mutate=samples.inherited_rotation(90)))

    work.run(Params(rotation=270))

    assert rotations(work.read("merged.pdf")) in ([0], [None])


@pytest.mark.parametrize(
    ("inherited", "ranges", "expected"),
    [
        # Intervalos sobrepostos com rotações diferentes: a página 2 sai duas vezes.
        (None, ((1, 2, 90), (2, 2, 0)), [90, 90, None]),
        (None, ((1, 1, 90), (1, 1, 0)), [90, None]),
        # A mesma rotação repetida não acumula.
        (None, ((1, 1, 90), (1, 1, 90)), [90, 90]),
        (None, ((1, 1, 0), (1, 1, 180)), [None, 180]),
        # Com /Rotate herdado da árvore, cada cópia parte da rotação da origem.
        (90, ((1, 1, 90), (1, 1, 0), (1, 1, 90)), [180, 90, 180]),
        (90, ((1, 1, 0), (1, 1, 270)), [90, None]),
    ],
)
def test_repeticao_com_rotacoes_diferentes(
    work: Workspace,
    inherited: int | None,
    ranges: tuple[tuple[int, int, int], ...],
    expected: list[int | None],
) -> None:
    mutate = None if inherited is None else samples.inherited_rotation(inherited)
    work.add("a.pdf", samples.document("a", pages=2, mutate=mutate), UPLOAD_A)

    work.run(
        Params(
            Operation.SELECT,
            select=tuple(PageRange(UPLOAD_A, first, last, turn) for first, last, turn in ranges),
        )
    )

    data = work.read("selection.pdf")
    # 0 explícito e ausência são a mesma rotação.
    assert [turn or None for turn in rotations(data)] == expected
    assert samples.markers(data) == [
        f"a:{page}" for first, last, _ in ranges for page in range(first, last + 1)
    ]


def test_split_girado_gira_cada_parte(work: Workspace) -> None:
    work.add("a.pdf", samples.document("a", pages=2))

    work.run(Params(Operation.SPLIT, rotation=180))

    assert rotations(work.read("a-p001.pdf")) == [180]
    assert rotations(work.read("a-p002.pdf")) == [180]


def test_strip_metadata_tira_autor_e_datas_das_anotacoes(work: Workspace) -> None:
    work.add("a.pdf", samples.document("a", mutate=samples.markup_annotation_with_author()))

    work.run(Params(strip_metadata=True))

    note, popup = annotation_keys(work.read("merged.pdf"))
    assert not note & set(ANNOTATION_METADATA)
    assert not popup & set(ANNOTATION_METADATA)
    assert "/Contents" in note
    with pikepdf.open(work.out_dir / "merged.pdf") as pdf:
        assert str(pdf.pages[0].obj.Annots[0].Contents) == "Nota"


def test_sem_strip_metadata_a_anotacao_fica_como_esta(work: Workspace) -> None:
    work.add("a.pdf", samples.document("a", mutate=samples.markup_annotation_with_author()))

    work.run()

    note, popup = annotation_keys(work.read("merged.pdf"))
    assert set(ANNOTATION_METADATA) <= note
    assert {"/T", "/M"} <= popup


@pytest.mark.parametrize("strip_metadata", [False, True])
def test_documento_composto_nunca_traz_info_nem_xmp_do_catalogo(
    work: Workspace, strip_metadata: bool
) -> None:
    work.add("a.pdf", samples.document("a", mutate=samples.document_info_and_catalog_xmp))

    work.run(Params(strip_metadata=strip_metadata))

    with pikepdf.open(work.out_dir / "merged.pdf") as pdf:
        assert "/Info" not in pdf.trailer
        assert "/Metadata" not in pdf.Root


# --- v2: compressão sem perdas e tamanho antes/depois (ADR 070) -------------------------------

LOSSLESS = Params(compression=Compression.LOSSLESS)


def test_lossless_relata_tamanho_antes_e_depois(work: Workspace) -> None:
    work.add("a.pdf", samples.document("a", pages=2, compress=False))
    work.add("b.pdf", samples.document("b", compress=False))

    report = work.run(LOSSLESS)

    first, second = report.items
    assert isinstance(first, ItemOk)
    assert isinstance(second, ItemOk)
    before = first.counts["bytes_before_compression"]
    after = first.counts["bytes_after_compression"]
    assert before > after == len(work.read("merged.pdf"))
    assert set(second.counts) == {"pages", "output_pages"}
    assert report.details["compression"] == "lossless"
    assert {"streams_recompressed", "streams_kept"} <= set(report.details)


def test_lossless_soma_os_documentos_do_item(work: Workspace) -> None:
    work.add("a.pdf", samples.document("a", pages=2, compress=False))

    report = work.run(Params(Operation.SPLIT, compression=Compression.LOSSLESS))

    [item] = report.items
    assert isinstance(item, ItemOk)
    sizes = len(work.read("a-p001.pdf")) + len(work.read("a-p002.pdf"))
    assert item.counts["bytes_after_compression"] == sizes


def test_lossless_conta_streams_recomprimidos_e_mantidos(work: Workspace) -> None:
    def poorly_compressed(pdf: pikepdf.Pdf) -> None:
        content = pdf.pages[0].obj.Contents
        content.write(zlib.compress(content.read_bytes() * 500, 0), filter=pikepdf.Name.FlateDecode)

    work.add("a.pdf", samples.document("a", mutate=poorly_compressed))

    report = work.run(LOSSLESS)

    assert report.details["streams_recompressed"] == 1
    assert report.details["streams_kept"] == 0


def test_lossless_gera_object_streams_e_versao_1_5(work: Workspace) -> None:
    work.add("a.pdf", samples.document("a", version="1.4"))

    work.run(LOSSLESS)

    merged = work.read("merged.pdf")
    assert merged.startswith(b"%PDF-1.5")
    with pikepdf.open(io.BytesIO(merged)) as pdf:
        assert (
            any(
                isinstance(o, pikepdf.Stream) and o.get("/Type") == pikepdf.Name.ObjStm
                for o in pdf.objects
            )
            or b"/ObjStm" in merged
        )


def test_sem_compressao_relatorio_igual_ao_da_v1(work: Workspace) -> None:
    work.add("a.pdf", samples.document("a"))

    report = work.run()

    [item] = report.items
    assert isinstance(item, ItemOk)
    assert set(item.counts) == {"pages", "output_pages"}
    assert "compression" not in report.details


def test_lossless_continua_deterministico(
    work: Workspace, tmp_path_factory: pytest.TempPathFactory
) -> None:
    other = Workspace(tmp_path_factory.mktemp("outra"))
    for workspace in (work, other):
        workspace.add("a.pdf", samples.document("a", pages=2, compress=False))
        workspace.run(LOSSLESS)

    assert work.read("merged.pdf") == other.read("merged.pdf")


def test_lossless_respeita_o_teto_de_objetos(work: Workspace) -> None:
    work.add("a.pdf", samples.document("a", mutate=samples.extra_objects(50)))

    with pytest.raises(RecipeFailure) as caught:
        work.run(LOSSLESS, recipe=PdfPress(limits=Limits(max_objects_per_document=20)))

    assert caught.value.code is FailureCode.INPUT_LIMIT_EXCEEDED
    assert work.output_paths() == []
