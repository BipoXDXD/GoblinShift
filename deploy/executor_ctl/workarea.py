"""Área de trabalho do executor: `<area>/<attempt_id>`, uma tentativa por vez (ADR 005).

O filesystem inteiro é a cota da tentativa, por isso o start recusa a área com qualquer coisa
além do diretório daquela tentativa (e do `lost+found` do ext4). O executor-ctl só mexe dentro
de `<area>/<attempt_id>`: prepara as permissões de escrita do executor e, no remove, apaga tudo,
inclusive o que o UID 61000 criou e o worker não consegue apagar.

O ctl roda como root, e o worker (menos privilegiado) controla o conteúdo de `<area>/<id>`. Por
isso nenhuma operação aqui segue symlink nem age sobre um caminho já conferido: cada diretório é
aberto com `O_DIRECTORY | O_NOFOLLOW` a partir do descritor do pai, conferido por `fstat` (é
diretório, no mesmo filesystem da área) e alterado por `fchown`/`fchmod` no próprio descritor.
A remoção usa o `rmtree` baseado em descritores, e o serviço recusa remover sem ele.

Janela residual: o `docker run` recebe `<area>/<id>` e `<id>/inputs` como caminho, e o daemon
os resolve sozinho. Logo antes do run, `confirm_unchanged` reconfere por lstat que os caminhos
ainda são os mesmos diretórios abertos; entre essa conferência e o daemon montar, um worker
comprometido que troque o diretório por symlink faria o Docker montar outro caminho do host em
/work (para o UID 61000 dentro do gVisor). O worker é a peça confiável do desenho (3.6).
"""

import errno
import os
import shutil
import stat
import uuid
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path

from .profile import INPUTS_DIR, OUTPUT_DIR

# O ext4 cria lost+found na raiz; ele não conta como resto de tentativa anterior.
FILESYSTEM_ENTRIES = frozenset({"lost+found"})
# setgid mantém o grupo nos arquivos novos; o grupo precisa ler, escrever e atravessar.
GROUP_WRITABLE_DIR_BITS = stat.S_ISGID | stat.S_IRWXG
_MAX_LISTED_ENTRIES = 5
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
# Abrir um symlink com O_NOFOLLOW dá ELOOP; um arquivo com O_DIRECTORY, ENOTDIR.
_NOT_A_REAL_DIRECTORY = frozenset({errno.ELOOP, errno.ENOTDIR, errno.ENOENT})


class WorkAreaDirtyError(Exception):
    """A área ainda tem arquivos de outra tentativa."""


class WorkDirMissingError(Exception):
    """O diretório da tentativa (ou inputs/, out/) não existe ou não é um diretório real."""


class UnsafeRemovalError(RuntimeError):
    """A plataforma não tem o rmtree resistente a symlink; o root não apaga sem ele."""


@dataclass(frozen=True)
class PreparedWorkDir:
    """Os diretórios conferidos no start, para reconferir logo antes do `docker run`."""

    path: Path
    work_dir_id: tuple[int, int]
    inputs_id: tuple[int, int]

    def confirm_unchanged(self) -> None:
        for path, expected in (
            (self.path, self.work_dir_id),
            (self.path / INPUTS_DIR, self.inputs_id),
        ):
            try:
                info = path.lstat()
            except FileNotFoundError as error:
                raise WorkDirMissingError(f"{path.name} sumiu durante o start") from error
            if not stat.S_ISDIR(info.st_mode) or (info.st_dev, info.st_ino) != expected:
                raise WorkDirMissingError(f"{path.name} foi trocado durante o start")


def attempt_dir(area: Path, attempt_id: uuid.UUID) -> Path:
    return area / str(attempt_id)


def prepare_for_start(area: Path, attempt_id: uuid.UUID, executor_gid: int) -> PreparedWorkDir:
    """Confere a área e deixa `<id>` e `<id>/out` graváveis pelo grupo do executor."""
    name = str(attempt_id)
    with _open_area(area) as area_fd:
        _require_clean(area_fd, name)
        area_dev = os.fstat(area_fd).st_dev
        with _open_subdir(area_fd, name, area_dev) as work_fd:
            with _open_subdir(work_fd, INPUTS_DIR, area_dev) as inputs_fd:
                inputs_id = _identity(inputs_fd)
            # Já existente (diretório ou não) é decidido pela abertura sem seguir link, abaixo.
            with suppress(FileExistsError):
                os.mkdir(OUTPUT_DIR, dir_fd=work_fd)
            with _open_subdir(work_fd, OUTPUT_DIR, area_dev) as output_fd:
                _make_group_writable(output_fd, executor_gid)
            _make_group_writable(work_fd, executor_gid)
            work_dir_id = _identity(work_fd)
    return PreparedWorkDir(attempt_dir(area, attempt_id), work_dir_id, inputs_id)


def remove_attempt_dir(area: Path, attempt_id: uuid.UUID) -> None:
    """Apaga `<area>/<id>` inteiro; já ausente é sucesso. Nunca segue symlink."""
    if not shutil.rmtree.avoids_symlink_attacks:
        raise UnsafeRemovalError("shutil.rmtree desta plataforma segue symlinks")
    name = str(attempt_id)
    if not area.is_dir():
        return
    with _open_area(area) as area_fd:
        try:
            info = os.stat(name, dir_fd=area_fd, follow_symlinks=False)
        except FileNotFoundError:
            return
        if stat.S_ISDIR(info.st_mode):
            # A versão por descritores recusa um topo que virou symlink e não segue os de dentro.
            shutil.rmtree(name, dir_fd=area_fd)
        else:
            # Symlink ou arquivo: só a entrada sai, o destino não é da tentativa.
            os.unlink(name, dir_fd=area_fd)


@contextmanager
def _open_area(area: Path) -> Iterator[int]:
    # A área vem da configuração do root; só ela é aberta pelo caminho.
    try:
        fd = os.open(area, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    except FileNotFoundError as error:
        raise WorkDirMissingError("a área de trabalho não existe") from error
    try:
        yield fd
    finally:
        os.close(fd)


@contextmanager
def _open_subdir(parent_fd: int, name: str, area_dev: int) -> Iterator[int]:
    try:
        fd = os.open(name, _DIR_FLAGS, dir_fd=parent_fd)
    except OSError as error:
        if error.errno in _NOT_A_REAL_DIRECTORY:
            raise WorkDirMissingError(
                f"{name} não existe ou não é diretório (symlink não vale)"
            ) from error
        raise
    try:
        info = os.fstat(fd)
        if not stat.S_ISDIR(info.st_mode) or info.st_dev != area_dev:
            raise WorkDirMissingError(f"{name} não é um diretório da área de trabalho")
        yield fd
    finally:
        os.close(fd)


def _require_clean(area_fd: int, own_entry: str) -> None:
    others = sorted(set(os.listdir(area_fd)) - FILESYSTEM_ENTRIES - {own_entry})
    if others:
        listed = ", ".join(others[:_MAX_LISTED_ENTRIES])
        raise WorkAreaDirtyError(f"a área tem {len(others)} entrada(s) de fora: {listed}")


def _identity(fd: int) -> tuple[int, int]:
    info = os.fstat(fd)
    return info.st_dev, info.st_ino


def _make_group_writable(fd: int, executor_gid: int) -> None:
    info = os.fstat(fd)
    if info.st_gid != executor_gid:
        os.fchown(fd, -1, executor_gid)
    wanted = stat.S_IMODE(info.st_mode) | GROUP_WRITABLE_DIR_BITS
    if stat.S_IMODE(info.st_mode) != wanted:
        os.fchmod(fd, wanted)
