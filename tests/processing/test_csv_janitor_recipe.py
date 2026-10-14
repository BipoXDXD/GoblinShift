"""Receita csv-janitor v1 sobre o contrato de 3.2 (docs/GoblinShiftPlan.md, 1.1, 1.3 e 1.7)."""

import hashlib
import unicodedata
from pathlib import Path

import pytest

from goblinshift.processing.csv_janitor import Limits, Params
from goblinshift.processing.csv_janitor.recipe import MAX_TABLES, CsvJanitorV1
from goblinshift.processing.output_dir import FileSystemOutputDir
from goblinshift.processing.recipe import (
    InputContractError,
    InputFile,
    ItemOk,
    ItemRejected,
    Report,
)
from goblinshift.processing.registry import RECIPES

VALID = "nome,cidade\r\nAna,Recife\r\n"
INVALID = 'nome,cidade\r\n"Ana,Recife\r\n'  # aspas que não fecham


class RecordedProgress:
    def __init__(self) -> None:
        self.done: list[int] = []

    def item_done(self, position: int) -> None:
        self.done.append(position)


class Workspace:
    def __init__(self, root: Path) -> None:
        self.inputs_dir = root / "inputs"
        self.inputs_dir.mkdir()
        self.out_dir = root / "out"
        self.out_dir.mkdir()
        self.inputs: list[InputFile] = []
        self.progress = RecordedProgress()

    def add(self, original_name: str, content: str | bytes) -> None:
        data = content.encode() if isinstance(content, str) else content
        position = len(self.inputs)
        path = self.inputs_dir / f"{position:04}"
        path.write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        self.inputs.append(InputFile(position, path, original_name, len(data), digest))

    def run(self, params: Params | None = None, recipe: CsvJanitorV1 | None = None) -> Report:
        return (recipe or CsvJanitorV1()).run(
            params or Params(), self.inputs, FileSystemOutputDir(self.out_dir), self.progress
        )

    def output_names(self) -> list[str]:
        return sorted(path.name for path in self.out_dir.iterdir())


@pytest.fixture
def work(tmp_path: Path) -> Workspace:
    return Workspace(tmp_path)


def test_tabela_valida_gera_um_csv_limpo_com_contagens(work: Workspace) -> None:
    work.add("clientes.csv", VALID)

    report = work.run()

    assert report.items == [
        ItemOk(
            position=0,
            outputs=("clientes.csv",),
            warnings=(),
            counts={
                "records_read": 1,
                "filtered_out": 0,
                "duplicates_removed": 0,
                "written": 1,
                "blank_lines_ignored": 0,
            },
        )
    ]
    assert (work.out_dir / "clientes.csv").read_bytes() == VALID.encode()
    assert work.progress.done == [0]
    assert report.has_outputs


def test_tabela_invalida_e_rejeitada_com_o_codigo_da_biblioteca(work: Workspace) -> None:
    work.add("ruim.csv", INVALID)

    report = work.run()

    [item] = report.items
    assert isinstance(item, ItemRejected)
    assert (item.position, item.code) == (0, "CSV_STRUCTURE_INVALID")
    assert item.reason
    assert work.output_names() == []
    assert work.progress.done == [0]


def test_mistura_mantem_os_itens_na_ordem_das_entradas(work: Workspace) -> None:
    work.add("a.csv", VALID)
    work.add("b.csv", INVALID)
    work.add("c.csv", VALID)

    report = work.run()

    assert [type(item) for item in report.items] == [ItemOk, ItemRejected, ItemOk]
    assert [item.position for item in report.items] == [0, 1, 2]
    assert work.output_names() == ["a.csv", "c.csv"]
    assert work.progress.done == [0, 1, 2]


def test_todas_rejeitadas_devolve_o_relatorio_sem_saidas_em_vez_de_falhar(
    work: Workspace,
) -> None:
    work.add("a.csv", INVALID)
    work.add("b.csv", "nome\r\nJosé\r\n".encode("latin-1"))

    report = work.run()

    assert all(isinstance(item, ItemRejected) for item in report.items)
    assert not report.has_outputs
    assert work.output_names() == []


def test_limite_da_tabela_rejeita_com_field_limit_exceeded(work: Workspace) -> None:
    work.add("grande.csv", "nome\r\na\r\nb\r\n")

    report = work.run(recipe=CsvJanitorV1(limits=Limits(records=1)))

    [item] = report.items
    assert isinstance(item, ItemRejected)
    assert item.code == "FIELD_LIMIT_EXCEEDED"


def test_tabela_um_byte_acima_do_tamanho_e_rejeitada(work: Workspace) -> None:
    work.add("a.csv", VALID)
    size = len(VALID.encode())

    at_limit = work.run(recipe=CsvJanitorV1(limits=Limits(table_bytes=size)))

    assert isinstance(at_limit.items[0], ItemOk)
    for path in work.out_dir.iterdir():
        path.unlink()
    above = work.run(recipe=CsvJanitorV1(limits=Limits(table_bytes=size - 1)))
    assert isinstance(above.items[0], ItemRejected)


def test_avisos_da_tabela_ficam_no_item(work: Workspace) -> None:
    work.add("a.csv", "conta\r\n=1+1\r\n")

    [item] = work.run().items

    assert isinstance(item, ItemOk)
    assert [warning.code for warning in item.warnings] == ["CSV_FORMULA_CELLS"]


def test_cinco_tabelas_sao_aceitas(work: Workspace) -> None:
    for index in range(MAX_TABLES):
        work.add(f"t{index}.csv", VALID)

    report = work.run()

    assert MAX_TABLES == 5
    assert len(report.items) == MAX_TABLES


def test_seis_tabelas_falham_antes_de_processar_qualquer_uma(work: Workspace) -> None:
    for index in range(MAX_TABLES + 1):
        work.add(f"t{index}.csv", VALID)

    with pytest.raises(InputContractError):
        work.run()

    assert work.output_names() == []
    assert work.progress.done == []


@pytest.mark.parametrize(
    ("original", "expected"),
    [
        ("clientes.csv", "clientes.csv"),
        ("clientes.TXT", "clientes.csv"),
        ("clientes", "clientes.csv"),
        ("vendas.2026.csv", "vendas.2026.csv"),
        ("report.json", "report.csv"),
        (".csv", "csv.csv"),
        ("..", "tabela.csv"),
        ("a\x01b.csv", "a_b.csv"),
    ],
)
def test_nome_da_saida_deriva_do_nome_original(
    work: Workspace, original: str, expected: str
) -> None:
    work.add(original, VALID)

    [item] = work.run().items

    assert isinstance(item, ItemOk)
    assert item.outputs == (expected,)


def test_nomes_que_colidem_por_caixa_recebem_sufixo_pelo_indice(work: Workspace) -> None:
    work.add("a.csv", VALID)
    work.add("A.CSV", VALID)

    report = work.run()

    assert [item.outputs for item in report.items if isinstance(item, ItemOk)] == [
        ("a.csv",),
        ("A_1.csv",),
    ]


def test_nomes_que_colidem_por_normalizacao_recebem_sufixo(work: Workspace) -> None:
    work.add(unicodedata.normalize("NFC", "Café.csv"), VALID)
    work.add(unicodedata.normalize("NFD", "Café.csv"), VALID)

    report = work.run()

    assert [item.outputs for item in report.items if isinstance(item, ItemOk)] == [
        ("Café.csv",),
        ("Café_1.csv",),
    ]


def test_registro_executa_a_receita_a_partir_dos_parametros_resolvidos(
    work: Workspace,
) -> None:
    work.add("a.csv", "nome\r\nANA\r\n")
    raw: dict[str, object] = {"case_rules": [{"column": "nome", "mode": "lower"}]}

    report = RECIPES["csv-janitor", 1].execute(
        raw, work.inputs, FileSystemOutputDir(work.out_dir), work.progress
    )

    assert isinstance(report.items[0], ItemOk)
    assert (work.out_dir / "a.csv").read_bytes() == b"nome\r\nana\r\n"
