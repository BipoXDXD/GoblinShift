"""Coleta de `out/`: só arquivos regulares e listados no relatório, dentro dos tetos (3.6, 1.7)."""

import os
from pathlib import Path
from typing import Any

import pytest

from goblinshift.processing.recipe import ItemNoticeCode, ItemRejectionCode, ItemWarningCode
from goblinshift.runs.formula_cells import ColumnCells, FormulaCells
from goblinshift.runs.lifecycle import ErrorCode, PublishOutcome
from goblinshift.runs.worker.collection import (
    CollectionError,
    OutputLimitError,
    OutputLimits,
    collect_outputs,
)
from tests.runs.collection_support import (
    RECIPE,
    duplicate_item,
    ok_item,
    rejected_item,
    summary_of,
    write_out,
    write_report,
)


def test_coleta_saidas_na_ordem_do_relatorio(tmp_path: Path) -> None:
    out = tmp_path / "out"
    write_out(out, [ok_item(0, "b.csv"), ok_item(1, "a.csv")], {"a.csv": b"1", "b.csv": b"22"})

    collected = collect_outputs(out, recipe_id=RECIPE, input_count=2)

    assert [f.name for f in collected.outputs] == ["b.csv", "a.csv"]
    assert [f.size for f in collected.outputs] == [2, 1]
    assert collected.report.outcome is PublishOutcome.SUCCEEDED


@pytest.mark.parametrize(
    ("items", "files", "outcome"),
    [
        ([ok_item(0, "a.csv"), rejected_item(1)], {"a.csv": b"1"}, "succeeded_with_warnings"),
        ([ok_item(0, "a.csv", warnings=1)], {"a.csv": b"1"}, "succeeded_with_warnings"),
        ([rejected_item(0), rejected_item(1)], {}, "no_valid_outputs"),
    ],
)
def test_desfecho_sai_das_rejeicoes_e_avisos(
    tmp_path: Path, items: list[dict[str, Any]], files: dict[str, bytes], outcome: str
) -> None:
    out = tmp_path / "out"
    write_out(out, items, files)

    collected = collect_outputs(out, recipe_id=RECIPE, input_count=len(items))

    assert collected.report.outcome is PublishOutcome(outcome)


def test_symlink_em_out_e_recusado(tmp_path: Path) -> None:
    out = tmp_path / "out"
    (tmp_path / "secret").write_bytes(b"x")
    write_out(out, [ok_item(0, "a.csv")], {})
    os.symlink(tmp_path / "secret", out / "a.csv")

    with pytest.raises(CollectionError, match="regular"):
        collect_outputs(out, recipe_id=RECIPE, input_count=1)


def test_hardlink_em_out_e_recusado(tmp_path: Path) -> None:
    out = tmp_path / "out"
    (tmp_path / "outside").write_bytes(b"x")
    write_out(out, [ok_item(0, "a.csv")], {})
    os.link(tmp_path / "outside", out / "a.csv")

    with pytest.raises(CollectionError, match="hardlink"):
        collect_outputs(out, recipe_id=RECIPE, input_count=1)


def test_subdiretorio_fora_da_lista_de_grupos_e_recusado(tmp_path: Path) -> None:
    out = tmp_path / "out"
    write_out(out, [ok_item(0, "a.csv")], {"a.csv": b"1"})
    (out / "sub").mkdir()

    with pytest.raises(CollectionError, match="grupo"):
        collect_outputs(out, recipe_id=RECIPE, input_count=1)


# --- Grupos (ADR 019): um nível de subpasta, só com os nomes de OutputGroup ---------------------


def test_saidas_em_grupos_permitidos_sao_coletadas_com_o_caminho(tmp_path: Path) -> None:
    out = tmp_path / "out"
    files = {"images/a.jpg": b"img", "thumbnails/a.jpg": b"t", "tables/b.csv": b"cs"}
    write_out(
        out, [ok_item(0, "images/a.jpg", "thumbnails/a.jpg"), ok_item(1, "tables/b.csv")], files
    )

    collected = collect_outputs(out, recipe_id=RECIPE, input_count=2)

    assert [(f.name, f.size) for f in collected.outputs] == [
        ("images/a.jpg", 3),
        ("thumbnails/a.jpg", 1),
        ("tables/b.csv", 2),
    ]
    assert collected.outputs[0].path == out / "images" / "a.jpg"


@pytest.mark.parametrize("group", ["images", "thumbnails", "tables", "text", "archives"])
def test_cada_grupo_da_lista_fechada_e_aceito(tmp_path: Path, group: str) -> None:
    out = tmp_path / "out"
    write_out(out, [ok_item(0, f"{group}/x")], {f"{group}/x": b"1"})

    assert [f.name for f in collect_outputs(out, recipe_id=RECIPE, input_count=1).outputs] == [
        f"{group}/x"
    ]


def test_grupo_com_caixa_diferente_e_recusado(tmp_path: Path) -> None:
    out = tmp_path / "out"
    write_out(out, [ok_item(0, "Images/a.jpg")], {"Images/a.jpg": b"1"})

    with pytest.raises(CollectionError, match="grupo"):
        collect_outputs(out, recipe_id=RECIPE, input_count=1)


def test_segundo_nivel_de_subpasta_e_recusado(tmp_path: Path) -> None:
    out = tmp_path / "out"
    write_out(out, [ok_item(0, "images/a.jpg")], {"images/a.jpg": b"1"})
    (out / "images" / "sub").mkdir()
    (out / "images" / "sub" / "x").write_bytes(b"1")

    with pytest.raises(CollectionError, match="regular"):
        collect_outputs(out, recipe_id=RECIPE, input_count=1)


def test_grupo_que_e_symlink_para_fora_e_recusado(tmp_path: Path) -> None:
    out = tmp_path / "out"
    outside = tmp_path / "fora"
    outside.mkdir()
    (outside / "a.jpg").write_bytes(b"segredo")
    write_out(out, [ok_item(0, "images/a.jpg")], {})
    (out / "images").symlink_to(outside, target_is_directory=True)

    with pytest.raises(CollectionError, match="regular"):
        collect_outputs(out, recipe_id=RECIPE, input_count=1)


def test_symlink_dentro_do_grupo_e_recusado(tmp_path: Path) -> None:
    out = tmp_path / "out"
    (tmp_path / "secret").write_bytes(b"x")
    write_out(out, [ok_item(0, "images/a.jpg")], {"images/b.jpg": b"1"})
    os.symlink(tmp_path / "secret", out / "images" / "a.jpg")

    with pytest.raises(CollectionError, match="regular"):
        collect_outputs(out, recipe_id=RECIPE, input_count=1)


def test_hardlink_dentro_do_grupo_e_recusado(tmp_path: Path) -> None:
    out = tmp_path / "out"
    (tmp_path / "outside").write_bytes(b"x")
    write_out(out, [ok_item(0, "images/a.jpg")], {"images/b.jpg": b"1"})
    os.link(tmp_path / "outside", out / "images" / "a.jpg")

    with pytest.raises(CollectionError, match="hardlink"):
        collect_outputs(out, recipe_id=RECIPE, input_count=1)


def test_arquivo_solto_num_grupo_sem_citacao_e_recusado(tmp_path: Path) -> None:
    out = tmp_path / "out"
    write_out(out, [ok_item(0, "images/a.jpg")], {"images/a.jpg": b"1", "images/b.jpg": b"2"})

    with pytest.raises(CollectionError, match="citadas"):
        collect_outputs(out, recipe_id=RECIPE, input_count=1)


def test_report_json_dentro_de_um_grupo_e_conteudo_comum(tmp_path: Path) -> None:
    out = tmp_path / "out"
    write_out(out, [ok_item(0, "text/report.json")], {"text/report.json": b"{}"})

    collected = collect_outputs(out, recipe_id=RECIPE, input_count=1)

    assert [f.name for f in collected.outputs] == ["text/report.json"]


def test_tetos_contam_os_arquivos_de_todos_os_grupos(tmp_path: Path) -> None:
    out = tmp_path / "out"
    names = ["a.csv", "images/a.jpg", "thumbnails/a.jpg", "text/a.txt"]
    write_out(out, [ok_item(0, *names)], dict.fromkeys(names, b"1"))

    with pytest.raises(OutputLimitError) as caught:
        collect_outputs(out, recipe_id=RECIPE, input_count=1, limits=OutputLimits(max_files=3))

    assert caught.value.code is ErrorCode.OUTPUT_LIMIT_EXCEEDED


def test_bytes_dos_grupos_somam_no_teto(tmp_path: Path) -> None:
    out = tmp_path / "out"
    names = ["images/a.jpg", "thumbnails/a.jpg"]
    write_out(out, [ok_item(0, *names)], dict.fromkeys(names, b"12"))

    with pytest.raises(OutputLimitError) as caught:
        collect_outputs(out, recipe_id=RECIPE, input_count=1, limits=OutputLimits(max_bytes=3))

    assert caught.value.code is ErrorCode.OUTPUT_LIMIT_EXCEEDED


# --- Duplicatas do Detox: item aceito, sem saídas e sem aviso -----------------------------------


def test_duplicata_e_aceita_sem_aviso(tmp_path: Path) -> None:
    out = tmp_path / "out"
    write_out(out, [ok_item(0, "images/a.png"), duplicate_item(1, 0)], {"images/a.png": b"1"})

    collected = collect_outputs(out, recipe_id=RECIPE, input_count=2)

    assert collected.report.outcome is PublishOutcome.SUCCEEDED
    assert collected.report.items[1].outputs == ()
    assert collected.report.items[1].code is None


@pytest.mark.parametrize(
    "duplicate_of",
    [
        pytest.param(1, id="ela-mesma"),
        pytest.param(2, id="posterior"),
        pytest.param(-1, id="negativa"),
        pytest.param("0", id="texto"),
        pytest.param(True, id="booleano"),
    ],
)
def test_duplicata_que_nao_aponta_para_posicao_anterior_e_recusada(
    tmp_path: Path, duplicate_of: object
) -> None:
    out = tmp_path / "out"
    items = [ok_item(0, "a.png"), duplicate_item(1, duplicate_of), ok_item(2, "b.png")]
    write_out(out, items, {"a.png": b"1", "b.png": b"2"})

    with pytest.raises(CollectionError, match="duplicate_of"):
        collect_outputs(out, recipe_id=RECIPE, input_count=3)


def test_duplicata_de_item_rejeitado_e_recusada(tmp_path: Path) -> None:
    out = tmp_path / "out"
    items = [rejected_item(0), duplicate_item(1, 0), ok_item(2, "b.png")]
    write_out(out, items, {"b.png": b"2"})

    with pytest.raises(CollectionError, match="duplicate_of"):
        collect_outputs(out, recipe_id=RECIPE, input_count=3)


def test_excesso_de_arquivos_e_falha_deterministica(tmp_path: Path) -> None:
    out = tmp_path / "out"
    names = [f"{i}.csv" for i in range(4)]
    write_out(out, [ok_item(0, *names)], dict.fromkeys(names, b"1"))

    with pytest.raises(OutputLimitError) as caught:
        collect_outputs(out, recipe_id=RECIPE, input_count=1, limits=OutputLimits(max_files=3))

    assert caught.value.code is ErrorCode.OUTPUT_LIMIT_EXCEEDED


def test_quantidade_no_teto_e_aceita(tmp_path: Path) -> None:
    out = tmp_path / "out"
    names = [f"{i}.csv" for i in range(3)]
    write_out(out, [ok_item(0, *names)], dict.fromkeys(names, b"1"))

    collected = collect_outputs(
        out, recipe_id=RECIPE, input_count=1, limits=OutputLimits(max_files=3)
    )

    assert len(collected.outputs) == 3


def test_excesso_de_bytes_e_falha_deterministica(tmp_path: Path) -> None:
    out = tmp_path / "out"
    write_out(out, [ok_item(0, "a.csv", "b.csv")], {"a.csv": b"12", "b.csv": b"345"})

    with pytest.raises(OutputLimitError) as caught:
        collect_outputs(out, recipe_id=RECIPE, input_count=1, limits=OutputLimits(max_bytes=4))

    assert caught.value.code is ErrorCode.OUTPUT_LIMIT_EXCEEDED


def test_relatorio_acima_do_teto_e_falha_deterministica(tmp_path: Path) -> None:
    out = tmp_path / "out"
    write_out(out, [ok_item(0, "a.csv")], {"a.csv": b"1"})

    with pytest.raises(OutputLimitError) as caught:
        collect_outputs(
            out, recipe_id=RECIPE, input_count=1, limits=OutputLimits(report_max_bytes=10)
        )

    assert caught.value.code is ErrorCode.REPORT_LIMIT_EXCEEDED


def test_arquivo_fora_do_relatorio_e_recusado(tmp_path: Path) -> None:
    out = tmp_path / "out"
    write_out(out, [ok_item(0, "a.csv")], {"a.csv": b"1", "intruso.csv": b"2"})

    with pytest.raises(CollectionError, match="relatório"):
        collect_outputs(out, recipe_id=RECIPE, input_count=1)


def test_saida_citada_e_ausente_e_recusada(tmp_path: Path) -> None:
    out = tmp_path / "out"
    write_out(out, [ok_item(0, "a.csv")], {})

    with pytest.raises(CollectionError, match="relatório"):
        collect_outputs(out, recipe_id=RECIPE, input_count=1)


def test_relatorio_sem_um_item_por_entrada_e_recusado(tmp_path: Path) -> None:
    out = tmp_path / "out"
    write_out(out, [ok_item(0, "a.csv")], {"a.csv": b"1"})

    with pytest.raises(CollectionError, match="posições"):
        collect_outputs(out, recipe_id=RECIPE, input_count=2)


@pytest.mark.parametrize(
    "raw",
    [
        b"nao e json",
        b'{"schema_version": 2}',
        b'{"schema_version": 1, "schema_version": 1}',
        b"[]",
    ],
)
def test_relatorio_malformado_e_recusado(tmp_path: Path, raw: bytes) -> None:
    out = tmp_path / "out"
    out.mkdir()
    (out / "report.json").write_bytes(raw)

    with pytest.raises(CollectionError):
        collect_outputs(out, recipe_id=RECIPE, input_count=0)


def test_sem_relatorio_e_recusado(tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()

    with pytest.raises(CollectionError, match=r"report\.json"):
        collect_outputs(out, recipe_id=RECIPE, input_count=0)


# --- Código e summary do relatório: dado do sandbox, parse contra o catálogo e os itens --------


@pytest.mark.parametrize("code", list(ItemRejectionCode))
def test_codigo_de_rejeicao_do_catalogo_e_aceito(tmp_path: Path, code: ItemRejectionCode) -> None:
    out = tmp_path / "out"
    write_out(out, [ok_item(0, "a.csv"), rejected_item(1, code)], {"a.csv": b"1"})

    collected = collect_outputs(out, recipe_id=RECIPE, input_count=2)

    assert collected.report.items[1].code is code


@pytest.mark.parametrize(
    "code",
    [
        pytest.param("EXECUTOR_UNAVAILABLE", id="codigo-de-execucao"),
        pytest.param("OUTPUT_LIMIT_EXCEEDED", id="falha-deterministica"),
        pytest.param("unsupported_format", id="caixa-errada"),
        pytest.param("UNSUPPORTED_FORMAT ", id="espaco"),
        pytest.param("<script>alert(1)</script>", id="html"),
        pytest.param("X" * 100_000, id="enorme"),
        pytest.param(7, id="numero"),
        pytest.param(None, id="nulo"),
    ],
)
def test_codigo_de_rejeicao_fora_do_catalogo_e_recusado(tmp_path: Path, code: object) -> None:
    out = tmp_path / "out"
    write_out(out, [ok_item(0, "a.csv"), rejected_item(1, code)], {"a.csv": b"1"})

    with pytest.raises(CollectionError, match="code"):
        collect_outputs(out, recipe_id=RECIPE, input_count=2)


def test_summary_sai_dos_itens_conferidos(tmp_path: Path) -> None:
    out = tmp_path / "out"
    items = [ok_item(0, "a.png", "b.png"), rejected_item(1), duplicate_item(2, 0)]
    write_out(out, items, {"a.png": b"1", "b.png": b"2"})

    collected = collect_outputs(out, recipe_id=RECIPE, input_count=3)

    assert collected.report.summary.as_json() == {
        "inputs": 3,
        "ok": 1,
        "rejected": 1,
        "duplicates": 1,
        "outputs": 2,
    }


def test_summary_sem_duplicates_do_executor_anterior_a_fatia_5_e_aceito(tmp_path: Path) -> None:
    out = tmp_path / "out"
    items = [ok_item(0, "a.csv"), rejected_item(1)]
    write_out(out, items, {"a.csv": b"1"})
    old = summary_of(items)
    del old["duplicates"]
    write_report_again(out, items, old)

    collected = collect_outputs(out, recipe_id=RECIPE, input_count=2)

    assert collected.report.summary.duplicates == 0


def write_report_again(out: Path, items: list[dict[str, Any]], summary: object) -> None:
    (out / "report.json").unlink()
    write_report(out, items, summary)


_ABSENT = object()


def _summary_with(**changes: object) -> dict[str, object]:
    summary: dict[str, object] = {
        "inputs": 2,
        "ok": 1,
        "rejected": 1,
        "duplicates": 0,
        "outputs": 1,
    }
    summary.update(changes)
    return {key: value for key, value in summary.items() if value is not _ABSENT}


@pytest.mark.parametrize(
    "summary",
    [
        pytest.param(_summary_with(extra=1), id="chave-desconhecida"),
        pytest.param(_summary_with(inputs=_ABSENT), id="sem-inputs"),
        pytest.param(_summary_with(outputs=_ABSENT), id="sem-outputs"),
        pytest.param(_summary_with(ok="1"), id="texto"),
        pytest.param(_summary_with(ok=True), id="booleano"),
        pytest.param(_summary_with(ok=1.0), id="fracao"),
        pytest.param(_summary_with(rejected=-1), id="negativo"),
        pytest.param(_summary_with(outputs=10**30), id="enorme"),
        pytest.param(_summary_with(ok=2), id="nao-confere-com-os-itens"),
        pytest.param(_summary_with(duplicates=1), id="duplicata-inexistente"),
        pytest.param(_summary_with(nested={"a": 1}), id="objeto-aninhado"),
        pytest.param([], id="lista"),
    ],
)
def test_summary_fora_do_schema_ou_dos_itens_e_recusado(tmp_path: Path, summary: object) -> None:
    out = tmp_path / "out"
    items = [ok_item(0, "a.csv"), rejected_item(1)]
    write_out(out, items, {"a.csv": b"1"})
    write_report_again(out, items, summary)

    with pytest.raises(CollectionError, match="summary"):
        collect_outputs(out, recipe_id=RECIPE, input_count=2)


def test_summary_sem_duplicates_com_duplicata_e_recusado(tmp_path: Path) -> None:
    out = tmp_path / "out"
    items = [ok_item(0, "a.png"), duplicate_item(1, 0)]
    write_out(out, items, {"a.png": b"1"})
    summary = summary_of(items)
    del summary["duplicates"]
    write_report_again(out, items, summary)

    with pytest.raises(CollectionError, match="summary"):
        collect_outputs(out, recipe_id=RECIPE, input_count=2)


# --- Nomes que o executor deixou em out/ (dado não confiável, regras de 3.6) -----------------
# Um nome fora das regras estouraria o CHECK de artifact.file_name na publicação e repetiria o
# erro a cada tentativa; a coleta o recusa antes, como executor fora do contrato.


def test_nome_de_180_bytes_e_aceito(tmp_path: Path) -> None:
    out = tmp_path / "out"
    name = "images/" + "a" * 176 + ".jpg"
    write_out(out, [ok_item(0, name)], {name: b"1"})

    assert [f.name for f in collect_outputs(out, recipe_id=RECIPE, input_count=1).outputs] == [name]


@pytest.mark.parametrize(
    "name",
    [
        "images/" + "a" * 177 + ".jpg",  # 181 bytes no componente
        "images/" + "é" * 89 + ".jpg",  # 182 bytes em UTF-8, 93 caracteres
        "images/linha\nquebrada.jpg",
        "images/fatura\u202egpj.exe",  # RLO
        "images/a\u200bb.jpg",  # ZWSP
        "images/a\ufeffb.jpg",  # BOM no meio
        "images/a\u2066b\u2069.jpg",  # isolates
        "images/é.jpg",  # fora de NFC
        "a" * 177 + ".csv",
        "images/a\\b.jpg",  # barra invertida: separador no Windows e em leitores de ZIP
    ],
)
def test_nome_fora_das_regras_de_saida_e_recusado(tmp_path: Path, name: str) -> None:
    out = tmp_path / "out"
    write_out(out, [ok_item(0, name)], {name: b"1"})

    with pytest.raises(CollectionError, match="nome"):
        collect_outputs(out, recipe_id=RECIPE, input_count=1)


# --- Avisos e contagens por item: só o que o catálogo e o formato admitem (1.1, 3.6) --------------


def test_avisos_e_contagens_do_item_sao_coletados(tmp_path: Path) -> None:
    out = tmp_path / "out"
    counts = {"records_read": 3, "written": 2}
    item = ok_item(0, "a.csv", counts=counts)
    item["warnings"] = [
        {"code": "CSV_FORMULA_CELLS", "detail": "célula =1+1 na coluna 'nome de pessoa'"},
        {"code": "CSV_FORMULA_HEADER", "detail": "d"},
    ]
    write_out(out, [item, rejected_item(1)], {"a.csv": b"1"})

    collected = collect_outputs(out, recipe_id=RECIPE, input_count=2)

    first, second = collected.report.items
    assert first.warnings == (ItemWarningCode.CSV_FORMULA_CELLS, ItemWarningCode.CSV_FORMULA_HEADER)
    assert first.counts == counts
    assert first.has_warnings
    assert (second.warnings, second.counts) == ((), {})


@pytest.mark.parametrize(
    "warning",
    [
        pytest.param({"code": "INVENTADO", "detail": "d"}, id="codigo-fora-do-catalogo"),
        pytest.param({"code": 1, "detail": "d"}, id="codigo-nao-texto"),
        pytest.param("CSV_FORMULA_CELLS", id="aviso-nao-objeto"),
    ],
)
def test_aviso_fora_do_catalogo_e_recusado(tmp_path: Path, warning: object) -> None:
    out = tmp_path / "out"
    item = ok_item(0, "a.csv")
    item["warnings"] = [warning]
    write_out(out, [item], {"a.csv": b"1"})

    with pytest.raises(CollectionError, match="aviso"):
        collect_outputs(out, recipe_id=RECIPE, input_count=1)


@pytest.mark.parametrize(
    "counts",
    [
        pytest.param({"records_read": -1}, id="negativa"),
        pytest.param({"records_read": "3"}, id="texto"),
        pytest.param({"records_read": True}, id="booleano"),
        pytest.param({"Nome Pessoal": 1}, id="chave-fora-do-formato"),
        pytest.param({f"c{i}": 1 for i in range(17)}, id="chaves-demais"),
        pytest.param([1], id="nao-objeto"),
    ],
)
def test_contagem_fora_do_formato_e_recusada(tmp_path: Path, counts: object) -> None:
    out = tmp_path / "out"
    item = ok_item(0, "a.csv")
    item["counts"] = counts
    write_out(out, [item], {"a.csv": b"1"})

    with pytest.raises(CollectionError, match="counts"):
        collect_outputs(out, recipe_id=RECIPE, input_count=1)


def test_dezesseis_contagens_sao_aceitas(tmp_path: Path) -> None:
    out = tmp_path / "out"
    counts = {f"c{i}": i for i in range(16)}
    write_out(out, [ok_item(0, "a.csv", counts=counts)], {"a.csv": b"1"})

    collected = collect_outputs(out, recipe_id=RECIPE, input_count=1)

    assert collected.report.items[0].counts == counts


def formula_warning(columns: object) -> dict[str, Any]:
    return {"code": "CSV_FORMULA_CELLS", "detail": "d", "columns": columns}


def test_contagem_por_coluna_do_aviso_de_formula_e_coletada_com_teto(tmp_path: Path) -> None:
    out = tmp_path / "out"
    item = ok_item(0, "a.csv")
    columns = [{"name": f"c{i}", "cells": i + 1} for i in range(11)]
    item["warnings"] = [formula_warning(columns)]
    write_out(out, [item], {"a.csv": b"1"})

    collected = collect_outputs(out, recipe_id=RECIPE, input_count=1)

    expected = tuple(ColumnCells(f"c{i}", i + 1) for i in range(10, 0, -1))
    assert collected.report.items[0].formula_cells == FormulaCells(expected, 1)


def test_coluna_de_cabecalho_vazio_e_coletada_sem_nome(tmp_path: Path) -> None:
    out = tmp_path / "out"
    item = ok_item(0, "a.csv")
    item["warnings"] = [formula_warning([{"name": "", "cells": 2}])]
    write_out(out, [item], {"a.csv": b"1"})

    collected = collect_outputs(out, recipe_id=RECIPE, input_count=1)

    assert collected.report.items[0].formula_cells == FormulaCells((ColumnCells(None, 2),), 0)


def test_aviso_de_formula_sem_colunas_do_executor_anterior_e_aceito(tmp_path: Path) -> None:
    out = tmp_path / "out"
    write_out(out, [ok_item(0, "a.csv", warnings=1)], {"a.csv": b"1"})

    collected = collect_outputs(out, recipe_id=RECIPE, input_count=1)

    [item] = collected.report.items
    assert item.warnings == (ItemWarningCode.CSV_FORMULA_CELLS,)
    assert item.formula_cells is None


@pytest.mark.parametrize(
    "columns",
    [
        pytest.param({"a": 1}, id="nao-lista"),
        pytest.param([], id="vazia"),
        pytest.param(["a"], id="coluna-nao-objeto"),
        pytest.param([{"name": 1, "cells": 1}], id="nome-nao-texto"),
        pytest.param([{"cells": 1}], id="sem-nome"),
        pytest.param([{"name": "a", "cells": 0}], id="zero-celulas"),
        pytest.param([{"name": "a", "cells": "1"}], id="celulas-texto"),
        pytest.param([{"name": "a", "cells": True}], id="celulas-booleano"),
    ],
)
def test_contagem_por_coluna_fora_da_forma_e_recusada(tmp_path: Path, columns: object) -> None:
    out = tmp_path / "out"
    item = ok_item(0, "a.csv")
    item["warnings"] = [formula_warning(columns)]
    write_out(out, [item], {"a.csv": b"1"})

    with pytest.raises(CollectionError, match="columns"):
        collect_outputs(out, recipe_id=RECIPE, input_count=1)


# --- Previews: fora do pacote, citados pelo item, com tipo e tamanho conferidos (3.6) -----------

JPEG_PREVIEW = b"\xff\xd8\xff\xe0" + b"0" * 100
CSV_PREVIEW = b"nome\r\nAna\r\n"


def with_preview(path: str, of: str, output: str | None = None) -> dict[str, Any]:
    item = ok_item(0, output or of)
    item["previews"] = [{"path": path, "of": of}]
    return item


def test_previews_sao_coletados_a_parte_do_conteudo(tmp_path: Path) -> None:
    out = tmp_path / "out"
    item = with_preview("previews/a.jpg", "images/a.png")
    write_out(out, [item], {"images/a.png": b"png", "previews/a.jpg": JPEG_PREVIEW})

    collected = collect_outputs(out, recipe_id=RECIPE, input_count=1)

    assert [f.name for f in collected.outputs] == ["images/a.png"]
    [preview] = collected.previews
    assert (preview.file.name, preview.of, preview.file.size) == (
        "previews/a.jpg",
        "images/a.png",
        len(JPEG_PREVIEW),
    )


def test_amostra_csv_e_aceita(tmp_path: Path) -> None:
    out = tmp_path / "out"
    write_out(
        out,
        [with_preview("previews/a.csv", "a.csv")],
        {"a.csv": b"x", "previews/a.csv": CSV_PREVIEW},
    )

    [preview] = collect_outputs(out, recipe_id=RECIPE, input_count=1).previews

    assert preview.of == "a.csv"


def test_mapa_de_paginas_em_json_e_aceito(tmp_path: Path) -> None:
    out = tmp_path / "out"
    page_map = b'{"schema_version": 1, "path": "a.pdf", "pages": []}'
    write_out(
        out,
        [with_preview("previews/a.json", "a.pdf")],
        {"a.pdf": b"%PDF", "previews/a.json": page_map},
    )

    [preview] = collect_outputs(out, recipe_id=RECIPE, input_count=1).previews

    assert preview.of == "a.pdf"


def test_relatorio_sem_previews_do_executor_anterior_e_aceito(tmp_path: Path) -> None:
    out = tmp_path / "out"
    write_out(out, [ok_item(0, "a.csv")], {"a.csv": b"x"})

    assert collect_outputs(out, recipe_id=RECIPE, input_count=1).previews == ()


@pytest.mark.parametrize(
    ("path", "content"),
    [
        pytest.param("previews/a.jpg", b"<svg onload=alert(1)>", id="jpg-que-nao-e-jpeg"),
        pytest.param("previews/a.jpg", b"\xff\xd8\xff" + b"0" * (512 * 1024), id="jpg-grande"),
        pytest.param("previews/a.csv", b"\xff\xfe", id="csv-fora-de-utf8"),
        pytest.param("previews/a.csv", b"a\x00b", id="csv-com-nul"),
        pytest.param("previews/a.csv", b"a" * (64 * 1024 + 1), id="csv-acima-de-64-kib"),
        pytest.param("previews/a.html", b"<b>x</b>", id="extensao-fora-da-lista"),
        pytest.param("previews/a.json", b"[1, 2]", id="json-que-nao-e-objeto"),
        pytest.param("previews/a.json", b'{"a": 1, "a": 2}', id="json-com-chave-repetida"),
        pytest.param("previews/a.json", b"{" * 10, id="json-malformado"),
    ],
)
def test_preview_fora_do_formato_e_recusado(tmp_path: Path, path: str, content: bytes) -> None:
    out = tmp_path / "out"
    write_out(out, [with_preview(path, "a.csv")], {"a.csv": b"x", path: content})

    with pytest.raises(CollectionError, match="preview"):
        collect_outputs(out, recipe_id=RECIPE, input_count=1)


@pytest.mark.parametrize(
    ("path", "of"),
    [
        pytest.param("images/a.jpg", "a.csv", id="fora-do-grupo"),
        pytest.param("previews/a.jpg", "b.csv", id="de-saida-de-outro-item"),
    ],
)
def test_preview_mal_citado_e_recusado(tmp_path: Path, path: str, of: str) -> None:
    out = tmp_path / "out"
    write_out(out, [with_preview(path, of, "a.csv")], {"a.csv": b"x", path: JPEG_PREVIEW})

    with pytest.raises(CollectionError, match="preview"):
        collect_outputs(out, recipe_id=RECIPE, input_count=1)


def test_preview_nao_citado_e_recusado(tmp_path: Path) -> None:
    out = tmp_path / "out"
    write_out(out, [ok_item(0, "a.csv")], {"a.csv": b"x", "previews/a.jpg": JPEG_PREVIEW})

    with pytest.raises(CollectionError):
        collect_outputs(out, recipe_id=RECIPE, input_count=1)


def test_previews_contam_no_teto_de_arquivos(tmp_path: Path) -> None:
    out = tmp_path / "out"
    write_out(
        out,
        [with_preview("previews/a.jpg", "a.csv")],
        {"a.csv": b"x", "previews/a.jpg": JPEG_PREVIEW},
    )

    with pytest.raises(OutputLimitError):
        collect_outputs(out, limits=OutputLimits(max_files=1), recipe_id=RECIPE, input_count=1)


def test_notas_do_item_sao_coletadas_e_nao_viram_aviso(tmp_path: Path) -> None:
    out = tmp_path / "out"
    item = ok_item(0, "a.csv")
    item["notices"] = ["IMAGE_P3_CONVERTED"]
    write_out(out, [item], {"a.csv": b"1"})

    collected = collect_outputs(out, recipe_id=RECIPE, input_count=1)

    assert collected.report.items[0].notices == (ItemNoticeCode.IMAGE_P3_CONVERTED,)
    assert collected.report.outcome is PublishOutcome.SUCCEEDED


def test_nota_fora_da_lista_e_recusada(tmp_path: Path) -> None:
    out = tmp_path / "out"
    item = ok_item(0, "a.csv")
    item["notices"] = ["Ana <ana@x.com>"]
    write_out(out, [item], {"a.csv": b"1"})

    with pytest.raises(CollectionError, match="nota"):
        collect_outputs(out, recipe_id=RECIPE, input_count=1)
