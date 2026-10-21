"""Tipos do contrato entre executor e receitas (3.2) que carregam regra própria."""

from pathlib import Path

import pytest

from goblinshift.processing.csv_janitor.results import RejectionCode as CsvCode
from goblinshift.processing.csv_janitor.results import WarningCode as CsvWarningCode
from goblinshift.processing.downloads_detox.recipe import REJECTION_CODE as DETOX_CODE
from goblinshift.processing.image_forge.rejection import RejectionCode as ImageCode
from goblinshift.processing.media_mill.rejection import RejectionCode as MediaCode
from goblinshift.processing.pdf_press.recipe import NOT_SELECTED as PDF_NOT_SELECTED
from goblinshift.processing.recipe import (
    InputContractError,
    InputFile,
    ItemDuplicate,
    ItemOk,
    ItemRejected,
    ItemRejectionCode,
    ItemWarningCode,
    OutputGroup,
    Preview,
    Report,
    ReportWarning,
    require_max_inputs,
)


def test_duplicata_aponta_para_uma_posicao_anterior() -> None:
    item = ItemDuplicate(position=3, duplicate_of=0)

    assert item.duplicate_of == 0


@pytest.mark.parametrize(
    ("position", "duplicate_of"),
    [
        pytest.param(3, 3, id="ela-mesma"),
        pytest.param(3, 4, id="posterior"),
        pytest.param(3, -1, id="negativa"),
        pytest.param(0, 0, id="primeira-entrada"),
    ],
)
def test_duplicata_de_si_mesma_de_posterior_ou_negativa_nao_existe(
    position: int, duplicate_of: int
) -> None:
    with pytest.raises(ValueError, match="duplicate_of"):
        ItemDuplicate(position=position, duplicate_of=duplicate_of)


def test_duplicata_imediatamente_anterior_e_valida() -> None:
    assert ItemDuplicate(position=1, duplicate_of=0).position == 1


def test_relatorio_so_com_duplicatas_e_rejeicoes_nao_tem_saidas() -> None:
    report = Report(items=(ItemRejected(0, "UNSUPPORTED_FORMAT", "…"), ItemDuplicate(1, 0)))

    assert not report.has_outputs


def test_relatorio_com_um_item_aceito_tem_saidas() -> None:
    report = Report(items=(ItemOk(0, ("images/a.png",)), ItemDuplicate(1, 0)))

    assert report.has_outputs


def test_detalhes_comecam_vazios_no_item_e_no_relatorio() -> None:
    item = ItemOk(0, ("a.csv",))

    assert item.details == {}
    assert Report(items=(item,)).details == {}


def test_grupos_de_saida_sao_a_lista_fechada_do_plano() -> None:
    assert [group.value for group in OutputGroup] == [
        "images",
        "thumbnails",
        "tables",
        "text",
        "archives",
        "audio",
        "previews",
    ]


# --- Códigos de rejeição por item: lista fechada do catálogo de 1.6 -----------------------------


def test_codigos_de_rejeicao_de_cada_receita_estao_no_catalogo() -> None:
    used = {*CsvCode, *ImageCode, *MediaCode, DETOX_CODE}

    assert used <= {code.value for code in ItemRejectionCode}


@pytest.mark.parametrize("code", ["EXECUTOR_UNAVAILABLE", "", "csv_structure_invalid"])
def test_rejeicao_com_codigo_fora_do_catalogo_nao_existe(code: str) -> None:
    with pytest.raises(ValueError, match="catálogo"):
        ItemRejected(position=0, code=code, reason="r")


# --- Códigos de aviso: lista fechada, porque vão para o banco e para a API ---------------------


def test_codigos_de_aviso_de_cada_receita_estao_no_catalogo() -> None:
    used = {*CsvWarningCode, PDF_NOT_SELECTED.code}

    assert used <= {code.value for code in ItemWarningCode}


@pytest.mark.parametrize("code", ["W", "", "csv_formula_cells"])
def test_aviso_com_codigo_fora_do_catalogo_nao_existe(code: str) -> None:
    with pytest.raises(ValueError, match="catálogo"):
        ReportWarning(code=code, detail="d")


# --- Previews: arquivo derivado em previews/, de uma saída de conteúdo do próprio item ----------


def test_preview_aponta_para_uma_saida_do_proprio_item() -> None:
    item = ItemOk(0, outputs=("a.csv",), previews=(Preview("previews/a.csv", "a.csv"),))

    assert item.previews[0].of == "a.csv"


def test_preview_de_saida_que_o_item_nao_tem_nao_existe() -> None:
    with pytest.raises(ValueError, match="preview"):
        ItemOk(0, outputs=("a.csv",), previews=(Preview("previews/b.csv", "b.csv"),))


def test_preview_fora_do_grupo_previews_nao_existe() -> None:
    with pytest.raises(ValueError, match="previews/"):
        Preview("images/a.jpg", "images/a.jpg")


def test_nota_fora_da_lista_nao_existe() -> None:
    with pytest.raises(ValueError, match="nota"):
        ItemOk(0, outputs=("a.png",), notices=("QUALQUER",))  # type: ignore[arg-type]


# --- Teto de entradas da versão: job acima dele é infraestrutura, não conteúdo ------------------


def _inputs(count: int) -> list[InputFile]:
    return [InputFile(i, Path(f"{i:04}.bin"), f"{i}.csv", 1, "0" * 64) for i in range(count)]


def test_lote_no_teto_de_entradas_passa() -> None:
    require_max_inputs(_inputs(3), 3, "tabelas")


def test_lote_acima_do_teto_de_entradas_e_job_fora_do_contrato() -> None:
    with pytest.raises(InputContractError, match="4 tabelas; o limite por execução é 3"):
        require_max_inputs(_inputs(4), 3, "tabelas")
