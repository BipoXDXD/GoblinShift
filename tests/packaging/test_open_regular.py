"""Abertura das saídas do sandbox para o empacotamento: sem seguir link e sem bloquear em FIFO.

Um FIFO plantado no lugar da saída, depois de qualquer checagem de tipo, bloquearia o
`open(O_RDONLY)` até aparecer um escritor e prenderia a thread de quem empacota. O vigia
(`tests/fifo_support`) reprova o teste em vez de travar a suíte.
"""

import os
import stat
from pathlib import Path

import pytest

from goblinshift.processing.packaging.zip_writer import ZipSource, write_zip
from goblinshift.processing.regular_file import open_regular
from tests.fifo_support import fail_if_blocked, make_fifo

MIB = 1024 * 1024


def _open_fd_count() -> int:
    return len(os.listdir("/dev/fd"))


def test_abrir_fifo_e_recusado_sem_bloquear(tmp_path: Path) -> None:
    fifo = tmp_path / "saida.csv"
    make_fifo(fifo)

    with fail_if_blocked(fifo), pytest.raises(OSError, match="regular"):
        open_regular(fifo)

    assert stat.S_ISFIFO(fifo.lstat().st_mode)


def test_abrir_fifo_nao_vaza_descritor(tmp_path: Path) -> None:
    fifo = tmp_path / "saida.csv"
    make_fifo(fifo)
    before = _open_fd_count()

    with fail_if_blocked(fifo):
        for _ in range(10):
            with pytest.raises(OSError, match="regular"):
                open_regular(fifo)

    assert _open_fd_count() == before


def test_zip_com_saida_trocada_por_fifo_falha_sem_bloquear(tmp_path: Path) -> None:
    # A fonte é um arquivo comum na hora de listar e vira FIFO antes de o ZIP abri-la.
    fifo = tmp_path / "saida.csv"
    make_fifo(fifo)
    target = tmp_path / "pacote.zip"
    before = _open_fd_count()

    with fail_if_blocked(fifo), pytest.raises(OSError, match="regular"):
        write_zip(target, [ZipSource("saida.csv", fifo)], max_bytes=MIB)

    assert _open_fd_count() == before


def test_abrir_arquivo_comum_devolve_o_descritor_em_modo_bloqueante(tmp_path: Path) -> None:
    path = tmp_path / "saida.csv"
    path.write_bytes(b"abc")

    with open_regular(path) as source:
        assert os.get_blocking(source.fileno())
        assert source.read() == b"abc"


def test_abrir_symlink_continua_recusado(tmp_path: Path) -> None:
    alvo = tmp_path / "alvo.csv"
    alvo.write_bytes(b"abc")
    link = tmp_path / "saida.csv"
    link.symlink_to(alvo)

    with pytest.raises(OSError, match=r"symbolic|Too many"):
        open_regular(link)
