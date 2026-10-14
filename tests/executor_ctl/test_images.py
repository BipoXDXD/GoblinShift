"""Allowlist de imagens do executor, relida a cada start."""

import json
from pathlib import Path

import pytest

from executor_ctl.images import ImageNotAllowedError, require_allowed

DIGEST = "sha256:" + "b" * 64
IMAGE = f"ghcr.io/bipoxdxd/goblinshift-executor@{DIGEST}"
PREVIOUS = "ghcr.io/bipoxdxd/goblinshift-executor@sha256:" + "c" * 64


def write_allowlist(tmp_path: Path, content: object) -> Path:
    path = tmp_path / "images.json"
    path.write_text(json.dumps(content))
    return path


def test_imagem_na_allowlist_e_aceita(tmp_path: Path) -> None:
    require_allowed(write_allowlist(tmp_path, {"allowed": [PREVIOUS, IMAGE]}), IMAGE)


def test_imagem_fora_da_allowlist_e_recusada(tmp_path: Path) -> None:
    with pytest.raises(ImageNotAllowedError):
        require_allowed(write_allowlist(tmp_path, {"allowed": [PREVIOUS]}), IMAGE)


def test_allowlist_vazia_recusa_tudo(tmp_path: Path) -> None:
    with pytest.raises(ImageNotAllowedError):
        require_allowed(write_allowlist(tmp_path, {"allowed": []}), IMAGE)


def test_allowlist_ausente_recusa_tudo(tmp_path: Path) -> None:
    with pytest.raises(ImageNotAllowedError):
        require_allowed(tmp_path / "images.json", IMAGE)


@pytest.mark.parametrize(
    "content",
    [["lista solta"], {"allowed": IMAGE}, {"allowed": [1]}, {"permitidas": [IMAGE]}],
)
def test_allowlist_malformada_recusa_tudo(tmp_path: Path, content: object) -> None:
    with pytest.raises(ImageNotAllowedError):
        require_allowed(write_allowlist(tmp_path, content), IMAGE)


def test_allowlist_relida_a_cada_chamada(tmp_path: Path) -> None:
    path = write_allowlist(tmp_path, {"allowed": [PREVIOUS]})
    with pytest.raises(ImageNotAllowedError):
        require_allowed(path, IMAGE)

    write_allowlist(tmp_path, {"allowed": [PREVIOUS, IMAGE]})

    require_allowed(path, IMAGE)


def test_allowlist_por_symlink_e_recusada(tmp_path: Path) -> None:
    # O arquivo pertence ao usuário do deploy; um symlink não faz o root ler outro arquivo.
    real = write_allowlist(tmp_path, {"allowed": [IMAGE]})
    link = tmp_path / "link.json"
    link.symlink_to(real)

    with pytest.raises(ImageNotAllowedError):
        require_allowed(link, IMAGE)
