"""Deduplicação por SHA-256 e tamanho, só dentro do lote, primeira ocorrência vence (1.4)."""

import hashlib
from dataclasses import replace

from goblinshift.processing.downloads_detox import Params
from goblinshift.processing.recipe import ItemDuplicate, ItemOk, ItemRejected
from tests.downloads_detox import samples
from tests.downloads_detox.conftest import Workspace


def test_copia_exata_aponta_para_a_primeira_ocorrencia(work: Workspace) -> None:
    work.add("a.png", samples.png())
    work.add("b.png", samples.png())
    work.add("c.png", samples.png())

    report = work.run()

    assert report.items[1:] == [ItemDuplicate(1, 0), ItemDuplicate(2, 0)]
    assert work.output_paths() == ["images/a.png"]


def test_copia_com_outra_extensao_coerente_tambem_e_duplicata(work: Workspace) -> None:
    work.add("a.jpg", samples.jpeg())
    work.add("a.jpeg", samples.jpeg())

    report = work.run()

    assert report.items[1] == ItemDuplicate(1, 0)


def test_desligada_as_copias_saem_todas_com_sufixo(work: Workspace) -> None:
    work.add("Foto.png", samples.png())
    work.add("Foto.png", samples.png())

    report = work.run(Params(deduplicate=False))

    assert all(isinstance(item, ItemOk) for item in report.items)
    assert work.output_paths() == ["images/Foto.png", "images/Foto_1.png"]


def test_mesmo_tamanho_e_hash_diferente_nao_e_duplicata(work: Workspace) -> None:
    work.add("a.txt", b"aaaa")
    work.add("b.txt", b"bbbb")

    report = work.run()

    assert all(isinstance(item, ItemOk) for item in report.items)


def test_mesmo_hash_e_tamanho_diferente_nao_e_duplicata(work: Workspace) -> None:
    # Não acontece com SHA-256 de verdade; a chave é o par, como 1.4 define.
    first = work.add("a.txt", b"aaaa")
    second = work.add("b.txt", b"aaaaa")
    work.inputs[1] = replace(second, sha256=first.sha256)

    report = work.run()

    assert all(isinstance(item, ItemOk) for item in report.items)


def test_entrada_rejeitada_nunca_e_original_de_outra(work: Workspace) -> None:
    content = samples.text("mesmo conteúdo")
    work.add("a.png", content)  # recusada: texto com extensão de imagem
    work.add("a.txt", content)
    work.add("b.txt", content)

    report = work.run()

    assert isinstance(report.items[0], ItemRejected)
    assert isinstance(report.items[1], ItemOk)
    assert report.items[2] == ItemDuplicate(2, 1)


def test_hash_vem_do_contrato_e_nao_e_recalculado(work: Workspace) -> None:
    # O executor já conferiu o SHA-256; a receita usa o do InputFile.
    work.add("a.txt", b"um")
    second = work.add("b.txt", b"do")
    work.inputs[1] = replace(second, sha256=hashlib.sha256(b"um").hexdigest())

    report = work.run()

    assert report.items[1] == ItemDuplicate(1, 0)
