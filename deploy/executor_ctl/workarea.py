"""Área de trabalho do executor: `<area>/<attempt_id>`, uma tentativa por vez (ADR 005).

O filesystem inteiro é a cota da tentativa, por isso o start recusa a área com qualquer coisa
além do diretório daquela tentativa (e do `lost+found` do ext4). O executor-ctl só mexe dentro
de `<area>/<attempt_id>`: prepara as permissões de escrita do executor e, no remove, apaga tudo,
inclusive o que o UID 61000 criou e o worker não consegue apagar.

Limite aceito: as verificações usam lstat, e entre elas e o `docker run` quem tem escrita no
diretório (o worker) ainda poderia trocar um diretório por symlink. O worker é a peça confiável
do desenho (3.6); a verificação existe contra erro, não contra um worker comprometido.
"""

import os
import shutil
import stat
import uuid
from pathlib import Path

from .profile import INPUTS_DIR, OUTPUT_DIR

# O ext4 cria lost+found na raiz; ele não conta como resto de tentativa anterior.
FILESYSTEM_ENTRIES = frozenset({"lost+found"})
# setgid mantém o grupo nos arquivos novos; o grupo precisa ler, escrever e atravessar.
GROUP_WRITABLE_DIR_BITS = stat.S_ISGID | stat.S_IRWXG
_MAX_LISTED_ENTRIES = 5


class WorkAreaDirtyError(Exception):
    """A área ainda tem arquivos de outra tentativa."""


class WorkDirMissingError(Exception):
    """O diretório da tentativa (ou o inputs/ dentro dele) não existe ou não é um diretório."""


def attempt_dir(area: Path, attempt_id: uuid.UUID) -> Path:
    return area / str(attempt_id)


def prepare_for_start(area: Path, attempt_id: uuid.UUID, executor_gid: int) -> Path:
    """Confere a área e deixa `<id>` e `<id>/out` graváveis pelo grupo do executor."""
    work_dir = attempt_dir(area, attempt_id)
    _require_clean(area, work_dir.name)
    _require_directory(work_dir)
    _require_directory(work_dir / INPUTS_DIR)
    output_dir = work_dir / OUTPUT_DIR
    if not os.path.lexists(output_dir):
        output_dir.mkdir()
    _require_directory(output_dir)
    for directory in (work_dir, output_dir):
        _make_group_writable(directory, executor_gid)
    return work_dir


def remove_attempt_dir(area: Path, attempt_id: uuid.UUID) -> None:
    """Apaga `<area>/<id>` inteiro; já ausente é sucesso."""
    work_dir = attempt_dir(area, attempt_id)
    if not os.path.lexists(work_dir):
        return
    if work_dir.is_symlink() or not work_dir.is_dir():
        # Só o link sai: o destino dele não é da tentativa.
        work_dir.unlink()
        return
    # rmtree não segue symlinks dentro da árvore (no Linux, usa descritores com O_NOFOLLOW).
    shutil.rmtree(work_dir)


def _require_clean(area: Path, own_entry: str) -> None:
    try:
        entries = {entry.name for entry in area.iterdir()}
    except FileNotFoundError as error:
        raise WorkDirMissingError("a área de trabalho não existe") from error
    others = sorted(entries - FILESYSTEM_ENTRIES - {own_entry})
    if others:
        listed = ", ".join(others[:_MAX_LISTED_ENTRIES])
        raise WorkAreaDirtyError(f"a área tem {len(others)} entrada(s) de fora: {listed}")


def _require_directory(path: Path) -> None:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError as error:
        raise WorkDirMissingError(f"{path.name} não existe") from error
    if not stat.S_ISDIR(mode):
        raise WorkDirMissingError(f"{path.name} não é um diretório (symlink não vale)")


def _make_group_writable(directory: Path, executor_gid: int) -> None:
    info = directory.lstat()
    if info.st_gid != executor_gid:
        os.chown(directory, -1, executor_gid, follow_symlinks=False)
    wanted = stat.S_IMODE(info.st_mode) | GROUP_WRITABLE_DIR_BITS
    if stat.S_IMODE(info.st_mode) != wanted:
        directory.chmod(wanted)
