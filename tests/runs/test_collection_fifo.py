"""FIFO no lugar da saída do sandbox depois do `lstat` da coleta (TOCTOU).

A coleta confere o tipo de cada arquivo de out/ com `lstat` e só depois o abre. O sandbox ainda
pode, nessa janela, trocar o arquivo por um FIFO: um `open(O_RDONLY)` sem `O_NONBLOCK` bloquearia
a thread do worker até aparecer um escritor. O vigia reprova o teste em vez de travar a suíte.
"""

import os
import stat
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from goblinshift.runs.worker import collection
from goblinshift.runs.worker.collection import CollectionError, collect_outputs
from goblinshift.runs.worker.package_check import PackageError, verify_zip
from tests.fifo_support import fail_if_blocked, make_fifo
from tests.runs.collection_support import PACKAGE, RECIPE, ok_item, write_out, write_package

FILES = {"a.csv": b"nome\r\nAna\r\n"}


def _open_fd_count() -> int:
    return len(os.listdir("/dev/fd"))


def _valid_out(tmp_path: Path) -> Path:
    out = tmp_path / "out"
    write_out(out, [ok_item(0, "a.csv")], FILES)
    write_package(out)
    return out


def _swap_for_fifo_after_scan(monkeypatch: pytest.MonkeyPatch, target: Path) -> None:
    """Depois que `_scan` fez o `lstat`, o sandbox troca `target` por um FIFO."""
    real_scan = collection._scan

    def scan(*args: Any, **kwargs: Any) -> Any:
        found = real_scan(*args, **kwargs)
        target.unlink()
        make_fifo(target)
        return found

    monkeypatch.setattr(collection, "_scan", scan)


# a.csv: `_digested`; report.json e manifest.json: `_read_no_follow`; pacote: `_digested`.
@pytest.mark.parametrize("name", ["a.csv", "report.json", "manifest.json", PACKAGE])
def test_saida_trocada_por_fifo_depois_do_lstat_e_recusada_sem_bloquear(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    out = _valid_out(tmp_path)
    _swap_for_fifo_after_scan(monkeypatch, out / name)
    before = _open_fd_count()

    with fail_if_blocked(out / name), pytest.raises(CollectionError, match="regular"):
        collect_outputs(out, recipe_id=RECIPE, input_count=1)

    assert stat.S_ISFIFO((out / name).lstat().st_mode)
    assert _open_fd_count() == before


def test_preview_trocado_por_fifo_depois_do_lstat_e_recusado_sem_bloquear(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "out"
    item = ok_item(0, "images/a.png")
    item["previews"] = [{"path": "previews/a.jpg", "of": "images/a.png"}]
    write_out(out, [item], {"images/a.png": b"png", "previews/a.jpg": b"\xff\xd8\xff jpeg"})
    preview = out / "previews" / "a.jpg"
    _swap_for_fifo_after_scan(monkeypatch, preview)

    with fail_if_blocked(preview), pytest.raises(CollectionError, match="regular"):
        collect_outputs(out, recipe_id=RECIPE, input_count=1)


def test_verificacao_do_zip_recusa_fifo_sem_bloquear(tmp_path: Path) -> None:
    fifo = tmp_path / PACKAGE
    make_fifo(fifo)
    before = _open_fd_count()

    with fail_if_blocked(fifo), pytest.raises(PackageError, match="regular"):
        verify_zip(fifo, [], lambda: None)

    assert _open_fd_count() == before


def test_coleta_le_arquivos_comuns_em_modo_bloqueante(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # O `O_NONBLOCK` só existe para não prender num FIFO: quem lê o arquivo comum recebe o
    # descritor como sempre foi.
    out = _valid_out(tmp_path)
    blocking: list[bool] = []
    real_fdopen: Callable[..., Any] = os.fdopen

    def spying_fdopen(fd: int, *args: Any, **kwargs: Any) -> Any:
        blocking.append(os.get_blocking(fd))
        return real_fdopen(fd, *args, **kwargs)

    monkeypatch.setattr(os, "fdopen", spying_fdopen)

    collect_outputs(out, recipe_id=RECIPE, input_count=1)

    assert blocking
    assert all(blocking)
