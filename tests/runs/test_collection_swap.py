"""out/ trocado depois do `lstat` da coleta (TOCTOU, #442).

A varredura confere cada arquivo com `lstat` e só depois o lê. Nessa janela o sandbox ainda pode
trocar `out/`, um grupo ou o próprio arquivo: a leitura desce de `out/` por `dir_fd` sem seguir
link e confere o inode do arquivo aberto contra o que a varredura viu.
"""

import os
from pathlib import Path
from typing import Any

import pytest

from goblinshift.runs.worker import collection
from goblinshift.runs.worker.collection import (
    CollectionError,
    FileIdentity,
    collect_outputs,
    open_collected,
)
from tests.runs.collection_support import RECIPE, ok_item, write_out


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
