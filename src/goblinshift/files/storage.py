"""Storage de objetos no filesystem da VPS (docs/GoblinShiftPlan.md, 3.2 e 3.6).

Esconde o layout em disco: quem chama só conhece chaves lógicas (`uploads/<id>`,
`runs/<run>/g<geração>/<id>`). O caminho é sempre derivado de uma chave validada por allowlist,
nunca de um nome recebido do usuário.

Criação exclusiva sem expor arquivo parcial: o conteúdo vai para um temporário próprio
(`O_CREAT | O_EXCL`), recebe `fsync` e só então ganha a chave final por `os.link`, que falha com
`EEXIST` se a chave já existe. Um `rename` sobrescreveria em silêncio; o `link` é atômico e nunca
sobrescreve, em Linux e macOS.
"""

import hashlib
import os
import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from stat import S_ISREG
from typing import BinaryIO

# Segmentos só com caracteres seguros e sem começar por ponto: barra `.`, `..`, ocultos e o
# diretório de temporários. O comprimento total limita o caminho bem abaixo do PATH_MAX.
_KEY_SEGMENT = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9._-]*")
_MAX_KEY_LENGTH = 512
_TMP_DIR_NAME = ".tmp"
# Gravada na raiz pelo `storage-mark` (compose.yaml), no filesystem já montado.
MOUNT_MARKER = ".goblinshift-storage"
_DIR_MODE = 0o700
_FILE_MODE = 0o600


class StorageError(Exception):
    """Base dos erros do storage."""


class InvalidStorageKeyError(StorageError):
    def __init__(self, key: str) -> None:
        super().__init__(f"chave de storage inválida: {key!r}")


class ObjectAlreadyExistsError(StorageError):
    def __init__(self, key: str) -> None:
        super().__init__(f"já existe objeto na chave {key!r}")


class ObjectMissingError(StorageError):
    def __init__(self, key: str) -> None:
        super().__init__(f"objeto ausente na chave {key!r}")


class ContentTooLargeError(StorageError):
    def __init__(self, max_bytes: int) -> None:
        super().__init__(f"conteúdo passou de {max_bytes} bytes")
        self.max_bytes = max_bytes


@dataclass(frozen=True)
class ObjectStat:
    size_bytes: int


class FileStorage:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self._tmp_dir = self.root / _TMP_DIR_NAME

    def create_exclusive(
        self, key: str, chunks: Iterable[bytes], max_bytes: int
    ) -> tuple[bytes, int]:
        """Grava o conteúdo sob `key` e devolve `(sha256, tamanho)`.

        Garante que nada aparece sob `key` se o conteúdo passar de `max_bytes`, se o iterador
        falhar ou se a chave já existir; o temporário é sempre removido.
        """
        final_path = self._path_for(key)
        if final_path.exists():
            raise ObjectAlreadyExistsError(key)
        self._make_private_dirs(final_path.parent)
        self._make_private_dirs(self._tmp_dir)
        tmp_path = self._tmp_dir / f"{uuid.uuid4().hex}.part"
        try:
            sha256, size = _write_exclusive(tmp_path, chunks, max_bytes)
            try:
                os.link(tmp_path, final_path)
            except FileExistsError:
                raise ObjectAlreadyExistsError(key) from None
        finally:
            tmp_path.unlink(missing_ok=True)
        _fsync_dir(final_path.parent)
        return sha256, size

    def open(self, key: str) -> BinaryIO:
        path = self._path_for(key)
        try:
            # O_NOFOLLOW: um symlink plantado sob a raiz não desvia a leitura para fora dela.
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            raise ObjectMissingError(key) from None
        return os.fdopen(fd, "rb")

    def stat(self, key: str) -> ObjectStat:
        try:
            info = self._path_for(key).stat(follow_symlinks=False)
        except FileNotFoundError:
            raise ObjectMissingError(key) from None
        return ObjectStat(size_bytes=info.st_size)

    def is_mounted(self) -> bool:
        """A sentinela de montagem existe na raiz, como arquivo comum.

        Com o filesystem do storage desmontado, a raiz some ou vira o ponto de montagem do disco
        do sistema, onde o `storage-init` recria a pasta e a aplicação volta a gravar; ter
        conteúdo não prova nada. A sentinela só é gravada pelo `storage-mark` depois de conferir
        a montagem (deploy/storage-mark.sh), e nenhuma chave pode criá-la, porque nenhum
        segmento de chave começa por ponto.
        """
        try:
            info = (self.root / MOUNT_MARKER).stat(follow_symlinks=False)
        except FileNotFoundError:
            return False
        return S_ISREG(info.st_mode)

    def delete(self, key: str) -> None:
        """Remove o objeto; remover o que já não existe é sucesso (o estado final é o mesmo)."""
        self._path_for(key).unlink(missing_ok=True)

    def _path_for(self, key: str) -> Path:
        if not _is_valid_key(key):
            raise InvalidStorageKeyError(key)
        return self.root.joinpath(*key.split("/"))

    def _make_private_dirs(self, directory: Path) -> None:
        # `mkdir(parents=True)` ignora `mode` nos pais; cria um nível por vez para que todos
        # nasçam privados.
        missing = [d for d in [directory, *directory.parents] if not d.exists()]
        for path in reversed(missing):
            path.mkdir(mode=_DIR_MODE, exist_ok=True)


def _is_valid_key(key: str) -> bool:
    if not key or len(key) > _MAX_KEY_LENGTH:
        return False
    return all(_KEY_SEGMENT.fullmatch(segment) for segment in key.split("/"))


def _write_exclusive(path: Path, chunks: Iterable[bytes], max_bytes: int) -> tuple[bytes, int]:
    digest = hashlib.sha256()
    size = 0
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, _FILE_MODE)
    with os.fdopen(fd, "wb") as out:
        for chunk in chunks:
            size += len(chunk)
            if size > max_bytes:
                raise ContentTooLargeError(max_bytes)
            digest.update(chunk)
            out.write(chunk)
        out.flush()
        os.fsync(out.fileno())
    return digest.digest(), size


def _fsync_dir(directory: Path) -> None:
    """Persiste a entrada de diretório criada pelo link; sem isso uma queda pode perdê-la."""
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
