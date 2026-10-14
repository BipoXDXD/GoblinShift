"""Receita downloads-detox v1 sobre o contrato de 3.2 (docs/GoblinShiftPlan.md, 1.1, 1.4, 1.7)."""

import unicodedata

import pytest

from goblinshift.processing.downloads_detox import GroupBy, NameMode, Params
from goblinshift.processing.downloads_detox.recipe import (
    MAX_INPUTS,
    MAX_TOTAL_INPUT_BYTES,
    OPAQUE_ARCHIVE_NOTE,
    OPAQUE_ARCHIVES_LIMIT,
    DownloadsDetoxV1,
)
from goblinshift.processing.recipe import (
    FailureCode,
    InputContractError,
    ItemDuplicate,
    ItemOk,
    ItemRejected,
    RecipeFailure,
)
from tests.downloads_detox import samples
from tests.downloads_detox.conftest import Workspace

IMAGE = {"kind": "image"}
TABLE = {"kind": "table"}
TEXT = {"kind": "text"}
ARCHIVE = {"kind": "archive", "note": OPAQUE_ARCHIVE_NOTE}
MIB = 1024 * 1024


def test_exemplo_de_1_4_oito_entradas_com_duas_copias_geram_seis_arquivos(
    work: Workspace,
) -> None:
    work.add("Foto.png", samples.png(1))
    work.add("foto.PNG", samples.png(2))
    work.add("notas.txt", samples.text("comprar pão\n"))
    work.add("dados.csv", samples.text("a,b\n1,2\n"))
    work.add("ferramenta.zip", samples.jar())
    work.add("praia.jpg", samples.jpeg())
    work.add("Foto (1).png", samples.png(1))
    work.add("dados (1).csv", samples.text("a,b\n1,2\n"))

    report = work.run()

    assert report.items == [
        ItemOk(0, ("images/Foto.png",), details=IMAGE),
        ItemOk(1, ("images/foto_1.PNG",), details=IMAGE),
        ItemOk(2, ("text/notas.txt",), details=TEXT),
        ItemOk(3, ("tables/dados.csv",), details=TABLE),
        ItemOk(4, ("archives/ferramenta.zip",), details=ARCHIVE),
        ItemOk(5, ("images/praia.jpg",), details=IMAGE),
        ItemDuplicate(6, duplicate_of=0),
        ItemDuplicate(7, duplicate_of=3),
    ]
    assert len(work.output_paths()) == 6
    assert work.progress.done == list(range(8))


def test_bytes_de_cada_saida_sao_os_da_entrada(work: Workspace) -> None:
    contents = [samples.png(), samples.jpeg(), samples.text(), samples.jar(), samples.EMPTY_ZIP]
    for index, data in enumerate(contents):
        work.add(f"a{index}{('.png', '.jpg', '.txt', '.zip', '.zip')[index]}", data)

    report = work.run()

    outputs = [item.outputs[0] for item in report.items if isinstance(item, ItemOk)]
    assert [work.read(path) for path in outputs] == contents


def test_relatorio_registra_o_limite_declarado_do_zip_opaco(work: Workspace) -> None:
    work.add("a.txt", samples.text())

    report = work.run()

    assert report.details == {"opaque_archives": OPAQUE_ARCHIVES_LIMIT}
    assert report.warnings == ()


def test_zip_que_e_jar_sai_em_archives_com_os_mesmos_bytes(work: Workspace) -> None:
    work.add("ferramenta.zip", samples.jar())
    work.add("ferramenta.jar", samples.jar())

    report = work.run(Params(deduplicate=False))

    assert report.items[0] == ItemOk(0, ("archives/ferramenta.zip",), details=ARCHIVE)
    assert work.read("archives/ferramenta.zip") == samples.jar()
    rejected = report.items[1]
    assert isinstance(rejected, ItemRejected)
    assert rejected.code == "UNSUPPORTED_FORMAT"


def test_rejeicao_tem_codigo_e_motivo_e_nao_gera_saida(work: Workspace) -> None:
    work.add("foto.png", samples.jpeg())

    report = work.run()

    assert report.items == [
        ItemRejected(0, "UNSUPPORTED_FORMAT", "o conteúdo é JPEG, mas a extensão é .png")
    ]
    assert not report.has_outputs
    assert work.output_paths() == []
    assert work.progress.done == [0]


def test_nomes_que_so_diferem_na_caixa_recebem_sufixo_pela_posicao(work: Workspace) -> None:
    work.add("Foto.png", samples.png(1))
    work.add("foto.PNG", samples.png(2))

    work.run()

    assert work.output_paths() == ["images/Foto.png", "images/foto_1.PNG"]


def test_nomes_em_nfc_e_nfd_colidem(work: Workspace) -> None:
    work.add("Relatório.txt", samples.text("um"))
    work.add(unicodedata.normalize("NFD", "Relatório.txt"), samples.text("dois"))

    work.run()

    assert work.output_paths() == ["text/Relatório.txt", "text/Relatório_1.txt"]


def test_mesmo_radical_em_grupos_diferentes_nao_colide(work: Workspace) -> None:
    work.add("a.csv", samples.text("x"))
    work.add("a.txt", samples.text("y"))

    work.run()

    assert work.output_paths() == ["tables/a.csv", "text/a.txt"]


def test_sem_grupos_nomes_de_tipos_diferentes_colidem_so_se_iguais(work: Workspace) -> None:
    work.add("a.csv", samples.text("x"))
    work.add("A.CSV", samples.text("y"))
    work.add("a.txt", samples.text("z"))

    work.run(Params(group_by=GroupBy.NONE))

    assert work.output_paths() == ["A_1.CSV", "a.csv", "a.txt"]


def test_nome_do_pacote_na_raiz_ganha_sufixo(work: Workspace) -> None:
    work.add("x.txt", samples.text())
    work.add("downloads-detox.zip", samples.EMPTY_ZIP)

    report = work.run(Params(group_by=GroupBy.NONE))

    assert report.items[1] == ItemOk(1, ("downloads-detox_1.zip",), details=ARCHIVE)


def test_nome_do_pacote_dentro_de_archives_nao_e_reservado(work: Workspace) -> None:
    work.add("downloads-detox.zip", samples.EMPTY_ZIP)

    work.run()

    assert work.output_paths() == ["archives/downloads-detox.zip"]


@pytest.mark.parametrize("name", ["manifest.json", "report.json"])
def test_entrada_com_nome_do_manifesto_ou_relatorio_e_recusada_pela_extensao(
    work: Workspace, name: str
) -> None:
    work.add(name, samples.text("{}"))

    report = work.run(Params(group_by=GroupBy.NONE))

    assert isinstance(report.items[0], ItemRejected)
    assert work.output_paths() == []


def test_nome_perigoso_vira_componente_seguro(work: Workspace) -> None:
    work.add("../../etc/pass\x00wd.txt", samples.text())

    report = work.run()

    assert report.items[0] == ItemOk(0, ("text/_.._etc_pass_wd.txt",), details=TEXT)


def test_nome_de_300_bytes_e_encurtado_para_180_com_a_extensao(work: Workspace) -> None:
    work.add("é" * 150 + ".png", samples.png())

    work.run()

    [path] = work.output_paths()
    component = path.removeprefix("images/")
    assert component == "é" * 88 + ".png"
    assert len(component.encode()) == 180


def test_indexed_mantem_o_indice_original_apesar_de_rejeicoes(work: Workspace) -> None:
    work.add("praia.png", samples.png())
    work.add("dados.csv", samples.text("a\n"))
    work.add("setup.exe", samples.PE_EXECUTABLE)
    work.add("notas.txt", samples.text("b\n"))
    work.add("foto.JPEG", samples.jpeg())

    report = work.run(Params(name_mode=NameMode.INDEXED))

    assert [type(item).__name__ for item in report.items] == [
        "ItemOk",
        "ItemOk",
        "ItemRejected",
        "ItemOk",
        "ItemOk",
    ]
    assert work.output_paths() == [
        "images/file-001.png",
        "images/file-005.jpg",
        "tables/file-002.csv",
        "text/file-004.txt",
    ]


def test_indexed_com_cem_entradas_chega_a_file_100(work: Workspace) -> None:
    for index in range(MAX_INPUTS):
        work.add(f"{index}.txt", samples.text(f"{index}"))

    work.run(Params(name_mode=NameMode.INDEXED, name_prefix="nota", group_by=GroupBy.NONE))

    paths = work.output_paths()
    assert (len(paths), paths[0], paths[-1]) == (100, "nota-001.txt", "nota-100.txt")


def test_acima_do_maximo_de_entradas_e_job_fora_do_contrato(work: Workspace) -> None:
    for index in range(MAX_INPUTS + 1):
        work.add(f"{index}.txt", samples.text())

    with pytest.raises(InputContractError, match="101"):
        work.run()
    assert work.output_paths() == []


def test_limite_publicado_do_lote_e_480_mib() -> None:
    assert MAX_TOTAL_INPUT_BYTES == 480 * MIB


def test_saidas_exatamente_no_limite_do_lote_passam(work: Workspace) -> None:
    work.add("a.txt", b"a" * 6)
    work.add("b.txt", b"b" * 4)

    report = work.run(recipe=DownloadsDetoxV1(max_total_bytes=10))

    assert report.has_outputs


def test_saidas_acima_do_limite_do_lote_falham_antes_de_copiar(work: Workspace) -> None:
    work.add("a.txt", b"a" * 6)
    work.add("b.txt", b"b" * 5)

    with pytest.raises(RecipeFailure) as caught:
        work.run(recipe=DownloadsDetoxV1(max_total_bytes=10))

    assert caught.value.code is FailureCode.OUTPUT_LIMIT_EXCEEDED
    assert work.output_paths() == []
    assert work.progress.done == []


def test_duplicatas_e_rejeicoes_nao_contam_para_o_limite_do_lote(work: Workspace) -> None:
    work.add("a.txt", b"a" * 10)
    work.add("b.txt", b"a" * 10)
    work.add("c.png", b"c" * 10)

    report = work.run(recipe=DownloadsDetoxV1(max_total_bytes=10))

    assert [type(item) for item in report.items] == [ItemOk, ItemDuplicate, ItemRejected]
