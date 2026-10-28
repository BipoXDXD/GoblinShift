"""Allowlist de imagens do executor, relida a cada start."""

import json
import os
import stat
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest

from executor_ctl.images import ImageNotAllowedError, require_allowed

pytestmark = pytest.mark.usefixtures("synthetic_policy_metadata")

DIGEST = "sha256:" + "b" * 64
IMAGE = f"ghcr.io/bipoxdxd/goblinshift-executor@{DIGEST}"
PREVIOUS = "ghcr.io/bipoxdxd/goblinshift-executor@sha256:" + "c" * 64


def write_allowlist(tmp_path: Path, content: object) -> Path:
    path = tmp_path.resolve() / "images.json"
    path.write_text(json.dumps(content))
    path.chmod(0o600)
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
    # Um symlink não pode fazer o leitor root adotar outro arquivo.
    real = write_allowlist(tmp_path, {"allowed": [IMAGE]})
    link = tmp_path.resolve() / "link.json"
    link.symlink_to(real)

    with pytest.raises(ImageNotAllowedError):
        require_allowed(link, IMAGE)


@pytest.mark.parametrize(
    "raw",
    [
        '{"allowed": [], "allowed": ["' + IMAGE + '"]}',
        json.dumps({"allowed": [IMAGE], "unknown": True}),
        json.dumps({"allowed": [IMAGE]}) + " " * 65536,
        "[" * 2000 + "]" * 2000,
    ],
)
def test_esquema_ambiguo_ou_grande_e_recusado(tmp_path: Path, raw: str) -> None:
    path = tmp_path.resolve() / "images.json"
    path.write_text(raw)
    path.chmod(0o600)
    with pytest.raises(ImageNotAllowedError):
        require_allowed(path, IMAGE)


@pytest.mark.parametrize(
    "target,change",
    [
        ("directory", {"uid": 1001}),
        ("directory", {"gid": 1001}),
        ("directory", {"mode": stat.S_IFDIR | 0o775}),
        ("leaf", {"uid": 1001}),
        ("leaf", {"gid": 1001}),
        ("leaf", {"mode": stat.S_IFREG | 0o644}),
        ("leaf", {"mode": stat.S_IFREG | 0o666}),
        ("leaf", {"nlink": 2}),
    ],
)
def test_metadados_inseguros_recusam_leitura(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
    change: dict[str, int],
    synthetic_policy_metadata: SimpleNamespace,
) -> None:
    path = write_allowlist(tmp_path, {"allowed": [PREVIOUS, IMAGE]})
    safe_stat: Callable[[int], os.stat_result] = synthetic_policy_metadata.fstat

    def unsafe_stat(fd: int) -> os.stat_result:
        info = safe_stat(fd)
        if (target == "directory" and info.st_ino == tmp_path.stat().st_ino) or (
            target == "leaf" and stat.S_ISREG(info.st_mode)
        ):
            fields = list(info)
            for name, index in (("mode", 0), ("nlink", 3), ("uid", 4), ("gid", 5)):
                fields[index] = change.get(name, fields[index])
            return os.stat_result(fields)
        return info

    monkeypatch.setattr(synthetic_policy_metadata, "fstat", unsafe_stat)
    with pytest.raises(ImageNotAllowedError):
        require_allowed(path, IMAGE)


def test_pai_symlink_e_recusado(tmp_path: Path) -> None:
    directory = tmp_path.resolve() / "real"
    directory.mkdir()
    write_allowlist(directory, {"allowed": [IMAGE]})
    alias = tmp_path.resolve() / "alias"
    alias.symlink_to(directory, target_is_directory=True)
    with pytest.raises(ImageNotAllowedError):
        require_allowed(alias / "images.json", IMAGE)


def test_hardlink_real_e_recusado(tmp_path: Path) -> None:
    path = write_allowlist(tmp_path, {"allowed": [IMAGE]})
    os.link(path, tmp_path / "other.json")
    with pytest.raises(ImageNotAllowedError):
        require_allowed(path, IMAGE)


def test_fifo_recusado_sem_bloquear(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    synthetic_policy_metadata: SimpleNamespace,
) -> None:
    fifo = tmp_path.resolve() / "images.json"
    os.mkfifo(fifo)
    # A guard must add O_NONBLOCK before opening a FIFO, then reject its type.
    real_open: Callable[..., int] = synthetic_policy_metadata.open

    def guarded_open(path: object, flags: int, *args: object, **kwargs: object) -> int:
        if str(path).endswith("images.json"):
            assert flags & os.O_NONBLOCK, "FIFO open could block the root reader"
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(synthetic_policy_metadata, "open", guarded_open)
    with pytest.raises(ImageNotAllowedError):
        require_allowed(fifo, IMAGE)


def test_leitura_continua_ancorada_se_nome_do_pai_for_trocado(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    synthetic_policy_metadata: SimpleNamespace,
) -> None:
    parent = tmp_path.resolve() / "protected"
    parent.mkdir()
    path = write_allowlist(parent, {"allowed": [PREVIOUS, IMAGE]})
    attacker = tmp_path.resolve() / "attacker"
    attacker.mkdir()
    write_allowlist(attacker, {"allowed": []})
    real_open: Callable[..., int] = synthetic_policy_metadata.open

    def replace_parent(path: object, flags: int, *args: object, **kwargs: object) -> int:
        if str(path) == "images.json":
            parent.rename(parent.with_name("opened"))
            parent.symlink_to(attacker, target_is_directory=True)
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(synthetic_policy_metadata, "open", replace_parent)
    require_allowed(path, IMAGE)


def test_pai_inseguro_e_recusado_antes_de_abrir_a_folha(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    synthetic_policy_metadata: SimpleNamespace,
) -> None:
    path = write_allowlist(tmp_path, {"allowed": [IMAGE]})
    safe_stat: Callable[[int], os.stat_result] = synthetic_policy_metadata.fstat
    real_open: Callable[..., int] = synthetic_policy_metadata.open

    def untrusted_parent(fd: int) -> os.stat_result:
        fields = list(safe_stat(fd))
        if fields[1] == tmp_path.stat().st_ino:
            fields[4] = 1001
        return os.stat_result(fields)

    def forbid_leaf(path: object, flags: int, *args: object, **kwargs: object) -> int:
        assert str(path) != "images.json", "untrusted ancestry reached leaf open"
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(synthetic_policy_metadata, "fstat", untrusted_parent)
    monkeypatch.setattr(synthetic_policy_metadata, "open", forbid_leaf)
    with pytest.raises(ImageNotAllowedError):
        require_allowed(path, IMAGE)


def test_decoder_recursion_failure_recusa_a_politica(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write_allowlist(tmp_path, {"allowed": [IMAGE]})

    def depth_limit(*args: object, **kwargs: object) -> object:
        raise RecursionError("Python 3.12 JSON decoder depth limit")

    monkeypatch.setattr(json, "loads", depth_limit)
    with pytest.raises(ImageNotAllowedError):
        require_allowed(path, IMAGE)
