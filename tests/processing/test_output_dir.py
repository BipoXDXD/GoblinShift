"""Nomes e tetos das saídas (docs/GoblinShiftPlan.md, 3.6 e 1.7)."""

import unicodedata
from pathlib import Path

import pytest

from goblinshift.processing.output_dir import FileSystemOutputDir, InvalidOutputNameError
from goblinshift.processing.recipe import FailureCode, OutputRole, RecipeFailure

CONTENT = OutputRole.CONTENT


def write(out: FileSystemOutputDir, name: str, position: int, data: bytes = b"x") -> str:
    output = out.create(name, CONTENT, position)
    with output.stream as stream:
        stream.write(data)
    return output.name


def test_cria_o_arquivo_com_o_nome_pedido_e_o_conteudo_escrito(tmp_path: Path) -> None:
    out = FileSystemOutputDir(tmp_path)

    name = write(out, "dados.csv", 0, b"a,b\r\n")

    assert name == "dados.csv"
    assert (tmp_path / "dados.csv").read_bytes() == b"a,b\r\n"
    assert out.created == (("dados.csv", CONTENT),)


def test_nome_em_nfd_e_gravado_em_nfc(tmp_path: Path) -> None:
    out = FileSystemOutputDir(tmp_path)

    name = write(out, unicodedata.normalize("NFD", "Café.csv"), 0)

    assert name == unicodedata.normalize("NFC", "Café.csv")
    assert [p.name for p in tmp_path.iterdir()] == [name]


def test_mesmo_nome_em_nfc_e_nfd_colide_e_recebe_sufixo_pelo_indice(tmp_path: Path) -> None:
    out = FileSystemOutputDir(tmp_path)

    first = write(out, unicodedata.normalize("NFC", "Café.csv"), 0)
    second = write(out, unicodedata.normalize("NFD", "Café.csv"), 3)

    assert first == "Café.csv"
    assert second == "Café_3.csv"


def test_nomes_que_diferem_so_na_caixa_colidem(tmp_path: Path) -> None:
    out = FileSystemOutputDir(tmp_path)

    write(out, "a.csv", 0)

    assert write(out, "A.CSV", 1) == "A_1.CSV"


def test_sufixo_que_tambem_colide_ganha_um_contador(tmp_path: Path) -> None:
    out = FileSystemOutputDir(tmp_path)
    write(out, "a_1.csv", 0)
    write(out, "a.csv", 2)

    assert write(out, "a.csv", 1) == "a_1_2.csv"


@pytest.mark.parametrize(
    ("reserved", "expected"),
    [
        ("report.json", "report_0.json"),
        ("manifest.json", "manifest_0.json"),
        ("REPORT.JSON", "REPORT_0.JSON"),
    ],
)
def test_nomes_reservados_recebem_sufixo(tmp_path: Path, reserved: str, expected: str) -> None:
    out = FileSystemOutputDir(tmp_path)

    assert write(out, reserved, 0) == expected


def test_nome_de_180_bytes_fica_inteiro(tmp_path: Path) -> None:
    out = FileSystemOutputDir(tmp_path)
    name = "é" * 88 + ".csv"  # 176 + 4 bytes
    assert len(name.encode()) == 180

    assert write(out, name, 0) == name


def test_nome_de_181_bytes_e_truncado_sem_partir_caractere_e_sem_perder_a_extensao(
    tmp_path: Path,
) -> None:
    out = FileSystemOutputDir(tmp_path)
    name = "a" + "é" * 88 + ".csv"  # 1 + 176 + 4 bytes
    assert len(name.encode()) == 181

    created = write(out, name, 0)

    assert created == "a" + "é" * 87 + ".csv"
    assert len(created.encode()) == 179


def test_sufixo_de_colisao_cabe_nos_180_bytes(tmp_path: Path) -> None:
    out = FileSystemOutputDir(tmp_path)
    name = "é" * 88 + ".csv"
    write(out, name, 0)

    created = write(out, name, 12)

    assert created.endswith("_12.csv")
    assert len(created.encode()) <= 180


@pytest.mark.parametrize(
    "name", ["", ".", "..", "a/b.csv", "a\\b.csv", "a\x00.csv", "a\nb.csv", "a\x7f.csv"]
)
def test_nome_vazio_com_separador_traversal_ou_controle_e_recusado(
    tmp_path: Path, name: str
) -> None:
    out = FileSystemOutputDir(tmp_path)

    with pytest.raises(InvalidOutputNameError):
        out.create(name, CONTENT, 0)

    assert list(tmp_path.iterdir()) == []


def test_nunca_sobrescreve_arquivo_existente(tmp_path: Path) -> None:
    (tmp_path / "dados.csv").write_bytes(b"original")
    out = FileSystemOutputDir(tmp_path)

    with pytest.raises(FileExistsError):
        out.create("dados.csv", CONTENT, 0)

    assert (tmp_path / "dados.csv").read_bytes() == b"original"


def test_quantidade_no_teto_e_aceita_e_acima_falha(tmp_path: Path) -> None:
    out = FileSystemOutputDir(tmp_path, max_files=2)
    write(out, "a.csv", 0)
    write(out, "b.csv", 1)

    with pytest.raises(RecipeFailure) as failure:
        out.create("c.csv", CONTENT, 2)

    assert failure.value.code is FailureCode.OUTPUT_LIMIT_EXCEEDED
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.csv", "b.csv"]


def test_bytes_no_teto_sao_aceitos_somados_entre_arquivos(tmp_path: Path) -> None:
    out = FileSystemOutputDir(tmp_path, max_bytes=10)

    write(out, "a.csv", 0, b"12345")
    write(out, "b.csv", 1, b"67890")

    assert (tmp_path / "b.csv").read_bytes() == b"67890"


def test_um_byte_acima_do_teto_falha_sem_gravar_o_excesso(tmp_path: Path) -> None:
    out = FileSystemOutputDir(tmp_path, max_bytes=10)
    write(out, "a.csv", 0, b"12345")

    with pytest.raises(RecipeFailure) as failure:
        write(out, "b.csv", 1, b"678901")

    assert failure.value.code is FailureCode.OUTPUT_LIMIT_EXCEEDED
    assert (tmp_path / "b.csv").read_bytes() == b""
