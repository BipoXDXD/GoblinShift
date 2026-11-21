"""out/ trocado depois do `lstat` da coleta (TOCTOU, #442).

A varredura confere cada arquivo com `lstat` e só depois o lê. Nessa janela o sandbox ainda pode
trocar `out/`, um grupo ou o próprio arquivo: a leitura desce de `out/` por `dir_fd` sem seguir
link e confere o inode do arquivo aberto contra o que a varredura viu.
"""

import os
from pathlib import Path
from typing import Any

import pytest

from goblinshift.runs.worker import collection, package_check
from goblinshift.runs.worker.collection import (
    CollectionError,
    FileIdentity,
    collect_outputs,
    open_collected,
)
from tests.runs.collection_support import (
    PACKAGE,
    RECIPE,
    entry,
    ok_item,
    package_entries,
    rewrite_package,
    write_out,
    write_package,
)


def _open_fd_count() -> int:
    return len(os.listdir("/dev/fd"))


def _after_scan(monkeypatch: pytest.MonkeyPatch, swap: Any) -> None:
    """Roda `swap()` logo depois do `_scan` real, como o sandbox faria entre `lstat` e `open`."""
    real_scan = collection._scan

    def scan(*args: Any, **kwargs: Any) -> Any:
        found = real_scan(*args, **kwargs)
        swap()
        return found

    monkeypatch.setattr(collection, "_scan", scan)


def _move_and_link(monkeypatch: pytest.MonkeyPatch, path: Path, moved: Path, target: Path) -> None:
    """Depois do `_scan`: `path` vai para `moved` e vira symlink para `target`."""

    def swap() -> None:
        path.rename(moved)
        path.symlink_to(target, target_is_directory=True)

    _after_scan(monkeypatch, swap)


# "fora": arquivo de fora, do mesmo tamanho; "proprio": o grupo verdadeiro, só que por um link
# (mesmo inode, então só a abertura sem seguir link pega).
@pytest.mark.parametrize("target", ["fora", "proprio"])
def test_grupo_trocado_por_symlink_depois_da_varredura_e_recusado(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    out = tmp_path / "out"
    write_out(out, [ok_item(0, "images/a.png")], {"images/a.png": b"png-real"})
    outside = tmp_path / "fora"
    outside.mkdir()
    (outside / "a.png").write_bytes(b"png-fora")
    moved = tmp_path / "real"
    _move_and_link(monkeypatch, out / "images", moved, outside if target == "fora" else moved)
    before = _open_fd_count()

    with pytest.raises(CollectionError, match="mudou"):
        collect_outputs(out, recipe_id=RECIPE, input_count=1)

    assert _open_fd_count() == before


@pytest.mark.parametrize("name", ["a.csv", "images/a.png"])
def test_saida_trocada_por_outro_arquivo_do_mesmo_tamanho_e_recusada(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    out = tmp_path / "out"
    write_out(out, [ok_item(0, name)], {name: b"original"})
    target = out / name

    def swap() -> None:
        # O substituto nasce antes de o original sumir: o inode é outro mesmo onde o sistema de
        # arquivos reaproveita inodes liberados.
        replacement = target.with_name("substituto")
        replacement.write_bytes(b"trocado!")
        replacement.replace(target)

    _after_scan(monkeypatch, swap)
    before = _open_fd_count()

    with pytest.raises(CollectionError, match="mudou"):
        collect_outputs(out, recipe_id=RECIPE, input_count=1)

    assert _open_fd_count() == before


def test_out_trocado_por_symlink_depois_da_varredura_e_recusado(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "out"
    write_out(out, [ok_item(0, "a.csv")], {"a.csv": b"nome\r\nAna\r\n"})
    moved = tmp_path / "real"
    # O link aponta para o out/ verdadeiro: os inodes batem, só a abertura de out/ sem seguir
    # link recusa.
    _move_and_link(monkeypatch, out, moved, moved)
    before = _open_fd_count()

    with pytest.raises(CollectionError, match="mudou"):
        collect_outputs(out, recipe_id=RECIPE, input_count=1)

    assert _open_fd_count() == before


def test_saida_coletada_guarda_a_identidade_do_lstat(tmp_path: Path) -> None:
    out = tmp_path / "out"
    write_out(out, [ok_item(0, "a.csv")], {"a.csv": b"nome\r\nAna\r\n"})

    collected = collect_outputs(out, recipe_id=RECIPE, input_count=1)

    info = (out / "a.csv").lstat()
    assert collected.outputs[0].identity == FileIdentity(info.st_dev, info.st_ino)


def test_open_collected_reabre_a_saida_coletada(tmp_path: Path) -> None:
    out = tmp_path / "out"
    write_out(out, [ok_item(0, "images/a.png")], {"images/a.png": b"png-real"})
    collected = collect_outputs(out, recipe_id=RECIPE, input_count=1)

    with open_collected(collected.outputs[0]) as source:
        assert source.read() == b"png-real"


def test_open_collected_recusa_saida_trocada_depois_da_coleta(tmp_path: Path) -> None:
    out = tmp_path / "out"
    write_out(out, [ok_item(0, "images/a.png")], {"images/a.png": b"png-real"})
    collected = collect_outputs(out, recipe_id=RECIPE, input_count=1)
    replacement = out / "images" / "substituto"
    replacement.write_bytes(b"png-fora")
    replacement.replace(out / "images" / "a.png")
    before = _open_fd_count()

    with pytest.raises(CollectionError, match="mudou"):
        open_collected(collected.outputs[0])

    assert _open_fd_count() == before


def test_pacote_trocado_e_devolvido_durante_a_verificacao_e_recusado(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A (fora do contrato) é o pacote da varredura; assim que a coleta o abre, o sandbox põe um B
    # válido no lugar e só devolve A (mesmo inode) depois da verificação. Verificar o que estiver
    # no caminho aprovaria B, e a cópia de A ainda bateria com o hash coletado: o hash e a
    # verificação precisam ler o mesmo A já aberto.
    out = tmp_path / "out"
    write_out(out, [ok_item(0, "a.csv")], {"a.csv": b"nome\r\nAna\r\n"})
    write_package(out)
    package = out / PACKAGE
    valid = package.read_bytes()
    entries = package_entries(out)
    entries[0] = entry("a.csv", b"nome\r\nBia\r\n")
    rewrite_package(out, entries)
    aside = tmp_path / "a-guardado.zip"
    swaps: list[str] = []
    real_open_found = collection._open_found
    real_verify = package_check.verify_zip

    def open_then_swap(found: Any) -> Any:
        source = real_open_found(found)
        if found.name == PACKAGE and not swaps:
            package.rename(aside)
            package.write_bytes(valid)
            swaps.append("B")
        return source

    def verify_then_restore(*args: Any, **kwargs: Any) -> None:
        try:
            real_verify(*args, **kwargs)
        finally:
            package.unlink()
            aside.rename(package)
            swaps.append("A")

    monkeypatch.setattr(collection, "_open_found", open_then_swap)
    monkeypatch.setattr(collection, "verify_zip", verify_then_restore)
    before = _open_fd_count()

    with pytest.raises(CollectionError, match="conteúdo diferente"):
        collect_outputs(out, recipe_id=RECIPE, input_count=1)

    assert swaps == ["B", "A"]
    assert _open_fd_count() == before
