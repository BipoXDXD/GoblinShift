"""Storage de objetos no filesystem da VPS (docs/GoblinShiftPlan.md, 3.2 e 3.6).

Esconde o layout em disco: quem chama só conhece chaves lógicas (`uploads/<id>`,
`runs/<run>/g<geração>/<id>`). O caminho é sempre derivado de uma chave validada por allowlist,
nunca de um nome recebido do usuário.

Criação exclusiva sem expor arquivo parcial: o conteúdo vai para um temporário próprio
(`O_CREAT | O_EXCL`), recebe `fsync` e só então ganha a chave final por `os.link`, que falha com
`EEXIST` se a chave já existe. Um `rename` sobrescreveria em silêncio; o `link` é atômico e nunca
sobrescreve, em Linux e macOS.

Queda do processo no meio da gravação deixa o temporário para trás: o `finally` não roda no
SIGKILL. `sweep_stale_temporaries` é a limpeza que a manutenção faz deles.

Abrir, consultar, gravar e remover descem da raiz um diretório por vez, relativos ao descritor do
pai e com `O_NOFOLLOW`: um symlink plantado em `uploads/` ou `runs/<run>/` não desvia nada para
fora da raiz. O caminho inteiro só protegeria o último nome.
"""

import contextlib
import errno
import fcntl
import hashlib
import logging
import os
import re
import uuid
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from stat import S_ISDIR, S_ISREG
from typing import BinaryIO

logger = logging.getLogger(__name__)

# Segmentos só com caracteres seguros e sem começar por ponto: barra `.`, `..`, ocultos e o
# diretório de temporários. O comprimento total limita o caminho bem abaixo do PATH_MAX.
_KEY_SEGMENT = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9._-]*")
_MAX_KEY_LENGTH = 512
_TMP_DIR_NAME = ".tmp"
# Só o nome que `create_exclusive` gera: a limpeza nunca apaga outra coisa do diretório.
_TMP_PART_NAME = re.compile(r"[0-9a-f]{32}\.part")
# Gravada na raiz pelo `storage-mark` (compose.yaml), no filesystem já montado.
MOUNT_MARKER = ".goblinshift-storage"
_DIR_MODE = 0o700
_FILE_MODE = 0o600
_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY
_SUBDIRECTORY_FLAGS = _DIRECTORY_FLAGS | os.O_NOFOLLOW


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


class ObjectTamperedError(StorageError):
    """A chave existe, mas não é um arquivo comum que o storage poderia ter criado.

    Symlink, diretório ou FIFO no lugar do objeto, ou symlink ou arquivo no lugar de um dos
    diretórios do caminho.

    Evidência de adulteração, não de perda: não é `ObjectMissingError`, para nenhuma varredura
    tratá-la como ausente. A mensagem leva só a chave lógica, nunca o caminho no disco.
    """

    def __init__(self, key: str) -> None:
        super().__init__(f"objeto adulterado na chave {key!r}: não é um arquivo comum")


class ContentTooLargeError(StorageError):
    def __init__(self, max_bytes: int) -> None:
        super().__init__(f"conteúdo passou de {max_bytes} bytes")
        self.max_bytes = max_bytes


@dataclass(frozen=True)
class ObjectStat:
    size_bytes: int


@dataclass(frozen=True)
class StoredFile:
    """Arquivo achado no percurso do disco; `modified_at` é o `st_mtime` (epoch, em s)."""

    key: str
    size_bytes: int
    modified_at: float


@dataclass(frozen=True)
class TemporarySweep:
    removed: int
    # O que ficou no diretório: gravações em curso e o que passou do teto da varredura.
    remaining: int
    remaining_bytes: int
    # Temporários vencidos que o sistema recusou apagar; também contam em `remaining`.
    failed: int = 0


class FileStorage:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def create_exclusive(
        self, key: str, chunks: Iterable[bytes], max_bytes: int
    ) -> tuple[bytes, int]:
        """Grava o conteúdo sob `key` e devolve `(sha256, tamanho)`.

        Garante que nada aparece sob `key` se o conteúdo passar de `max_bytes`, se o iterador
        falhar ou se a chave já existir; o temporário é sempre removido.
        """
        names = _names_of(key)
        self._make_private_root()
        parent_fd = self._open_directory(key, names[:-1], create=True)
        try:
            if _entry_exists(names[-1], parent_fd):
                raise ObjectAlreadyExistsError(key)
            sha256, size = self._link_new_object(key, names[-1], parent_fd, chunks, max_bytes)
            # Persiste a entrada criada pelo link; sem isso uma queda pode perdê-la.
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
        return sha256, size

    def _link_new_object(
        self, key: str, name: str, parent_fd: int, chunks: Iterable[bytes], max_bytes: int
    ) -> tuple[bytes, int]:
        tmp_fd = self._open_directory(key, [_TMP_DIR_NAME], create=True)
        tmp_name = f"{uuid.uuid4().hex}.part"
        try:
            sha256, size = _write_exclusive(tmp_name, tmp_fd, chunks, max_bytes)
            try:
                os.link(
                    tmp_name,
                    name,
                    src_dir_fd=tmp_fd,
                    dst_dir_fd=parent_fd,
                    follow_symlinks=False,
                )
            except FileExistsError:
                raise ObjectAlreadyExistsError(key) from None
        finally:
            try:
                with contextlib.suppress(FileNotFoundError):
                    os.unlink(tmp_name, dir_fd=tmp_fd)
            finally:
                os.close(tmp_fd)
        return sha256, size

    def open(self, key: str) -> BinaryIO:
        parent_fd, name = self._open_parent(key)
        try:
            # O_NOFOLLOW: um symlink plantado sob a raiz não desvia a leitura para fora dela.
            # O_NONBLOCK: um FIFO plantado no lugar do arquivo bloquearia o `open` até aparecer
            # um escritor, antes de qualquer checagem de tipo, e prenderia a thread de quem chama.
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_fd)
        except FileNotFoundError:
            raise ObjectMissingError(key) from None
        except OSError as error:
            if error.errno == errno.ELOOP:
                raise ObjectTamperedError(key) from None
            raise
        finally:
            os.close(parent_fd)
        try:
            # O_NOFOLLOW só barra symlink: diretório e FIFO abrem sem erro (o `fdopen` falharia
            # com `IsADirectoryError`, cru e com o descritor aberto). Confere o tipo no próprio fd.
            if not S_ISREG(os.fstat(fd).st_mode):
                raise ObjectTamperedError(key)
            # Só arquivo comum chega aqui: devolve o descritor ao modo bloqueante de sempre.
            fcntl.fcntl(fd, fcntl.F_SETFL, fcntl.fcntl(fd, fcntl.F_GETFL) & ~os.O_NONBLOCK)
            return os.fdopen(fd, "rb")
        except BaseException:
            os.close(fd)
            raise

    def stat(self, key: str) -> ObjectStat:
        """Tamanho do objeto; o que não é arquivo comum é adulteração, como em `open`.

        `lstat` não segue o link, e por isso enxerga um symlink (com ou sem alvo) ou um diretório
        como existentes: sem a checagem de tipo, eles passariam por objeto presente.
        """
        parent_fd, name = self._open_parent(key)
        try:
            info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            raise ObjectMissingError(key) from None
        finally:
            os.close(parent_fd)
        if not S_ISREG(info.st_mode):
            raise ObjectTamperedError(key)
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
        try:
            parent_fd, name = self._open_parent(key)
        except ObjectMissingError:
            return
        try:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(name, dir_fd=parent_fd)
        finally:
            os.close(parent_fd)

    def walk_objects(self) -> Iterator[StoredFile]:
        """Percorre o disco e devolve os arquivos que o storage poderia ter criado.

        Preguiçoso e sem ordem garantida. Só entram arquivos comuns sob nome de chave válido
        (`_KEY_SEGMENT`): o diretório `.tmp`, a sentinela de montagem, ocultos e qualquer nome
        que `create_exclusive` nunca grava ficam de fora, e symlinks nunca são seguidos. Arquivo
        ou diretório que some durante o percurso (o expurgo roda em paralelo) é ignorado; outro
        erro de disco sobe.
        """
        try:
            root_fd = os.open(self.root, _DIRECTORY_FLAGS)
        except FileNotFoundError:
            return
        yield from self._walk_directory(root_fd, "")

    def _walk_directory(self, directory_fd: int, prefix: str) -> Iterator[StoredFile]:
        """Percorre o diretório já aberto e fecha o descritor ao terminar.

        Cada subdiretório é aberto pelo nome, relativo ao descritor do pai e com `O_NOFOLLOW`:
        se ele virar symlink entre o `lstat` da entrada e a abertura, a abertura falha em vez de
        seguir para fora da raiz. O percurso pelo caminho teria essa corrida.
        """
        try:
            with os.scandir(directory_fd) as scan:
                for entry in scan:
                    found = self._classify(entry, prefix)
                    if isinstance(found, StoredFile):
                        yield found
                    elif found is not None:
                        child_fd = _open_subdirectory(entry.name, directory_fd)
                        if child_fd is not None:
                            yield from self._walk_directory(child_fd, found)
        finally:
            os.close(directory_fd)

    @staticmethod
    def _classify(entry: os.DirEntry[str], prefix: str) -> StoredFile | str | None:
        """Arquivo de chave válida, o prefixo do subdiretório a percorrer, ou `None` para pular."""
        if not _KEY_SEGMENT.fullmatch(entry.name):
            return None
        try:
            info = entry.stat(follow_symlinks=False)
        except FileNotFoundError:
            return None
        key = f"{prefix}{entry.name}"
        if S_ISDIR(info.st_mode):
            return f"{key}/"
        if S_ISREG(info.st_mode) and _is_valid_key(key):
            return StoredFile(key=key, size_bytes=info.st_size, modified_at=info.st_mtime)
        return None

    def sweep_stale_temporaries(self, modified_before: float, limit: int) -> TemporarySweep:
        """Apaga até `limit` temporários sem modificação desde `modified_before` (epoch, em s).

        O corte precisa ser maior que o intervalo em que uma gravação viva pode ficar sem escrever.
        Compara com o `mtime`, então `modified_before` vem do relógio da própria máquina. Falha
        ao abrir ou listar o diretório sobe; a de um arquivo fica no log e em `failed`, sem parar
        os demais, e conta no teto, que limita as tentativas e o log de cada varredura. Arquivo
        que some durante a varredura é sucesso (o estado final é o mesmo).

        `.tmp` é aberto a partir da raiz com `O_NOFOLLOW`, e a listagem e as remoções são
        relativas a esse descritor: um `.tmp` trocado por symlink ou arquivo é
        `ObjectTamperedError`, e nada fora da raiz é apagado.
        """
        try:
            tmp_fd = self._open_directory(_TMP_DIR_NAME, [_TMP_DIR_NAME], create=False)
        except FileNotFoundError:
            return TemporarySweep(removed=0, remaining=0, remaining_bytes=0)
        try:
            return _sweep_directory(tmp_fd, modified_before, limit)
        finally:
            os.close(tmp_fd)

    def _open_parent(self, key: str) -> tuple[int, str]:
        """Descritor do diretório do objeto e o nome dele lá dentro; quem chama fecha o descritor.

        `ObjectMissingError` se a raiz ou um diretório do caminho não existe.
        """
        names = _names_of(key)
        try:
            return self._open_directory(key, names[:-1], create=False), names[-1]
        except FileNotFoundError:
            raise ObjectMissingError(key) from None

    def _open_directory(self, key: str, names: Sequence[str], *, create: bool) -> int:
        """Abre `names` a partir da raiz, um nível por vez e sem seguir symlink.

        Com `create`, os níveis que faltam nascem privados. Um nível que é symlink ou arquivo é
        `ObjectTamperedError` de `key`; só um descritor fica aberto por vez, e nenhum sobra em
        caso de erro.
        """
        fd = os.open(self.root, _DIRECTORY_FLAGS)
        try:
            for name in names:
                parent_fd, fd = fd, _open_child_directory(name, fd, create=create)
                os.close(parent_fd)
        except OSError as error:
            os.close(fd)
            # ELOOP (Linux) ou ENOTDIR (macOS, e arquivo comum em qualquer sistema).
            if error.errno in (errno.ELOOP, errno.ENOTDIR):
                raise ObjectTamperedError(key) from None
            raise
        except BaseException:
            os.close(fd)
            raise
        return fd

    def _make_private_root(self) -> None:
        # A raiz vem da configuração e pode passar por symlink; abaixo dela, `_open_directory`.
        # `mkdir(parents=True)` ignora `mode` nos pais; cria um nível por vez para que todos
        # nasçam privados.
        missing = [d for d in [self.root, *self.root.parents] if not d.exists()]
        for path in reversed(missing):
            path.mkdir(mode=_DIR_MODE, exist_ok=True)


def _open_child_directory(name: str, parent_fd: int, *, create: bool) -> int:
    try:
        return os.open(name, _SUBDIRECTORY_FLAGS, dir_fd=parent_fd)
    except FileNotFoundError:
        if not create:
            raise
    # Outra gravação pode criar o mesmo diretório no meio: o dela serve.
    with contextlib.suppress(FileExistsError):
        os.mkdir(name, _DIR_MODE, dir_fd=parent_fd)
    return os.open(name, _SUBDIRECTORY_FLAGS, dir_fd=parent_fd)


def _entry_exists(name: str, parent_fd: int) -> bool:
    try:
        os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return True


def _open_subdirectory(name: str, parent_fd: int) -> int | None:
    """Descritor do subdiretório, ou `None` se ele sumiu ou deixou de ser diretório comum."""
    try:
        return os.open(name, _SUBDIRECTORY_FLAGS, dir_fd=parent_fd)
    except FileNotFoundError:
        return None
    except OSError as error:
        # ELOOP: virou symlink; ENOTDIR: virou arquivo. Os dois ficam fora, como no `_classify`.
        if error.errno in (errno.ELOOP, errno.ENOTDIR):
            return None
        raise


def _sweep_directory(tmp_fd: int, modified_before: float, limit: int) -> TemporarySweep:
    removed = 0
    failed = 0
    remaining = 0
    remaining_bytes = 0
    for name, info in _temporary_parts(tmp_fd):
        if info.st_mtime < modified_before and removed + failed < limit:
            if _unlink_temporary(name, tmp_fd):
                removed += 1
                continue
            failed += 1
        remaining += 1
        remaining_bytes += info.st_size
    return TemporarySweep(removed, remaining, remaining_bytes, failed)


def _temporary_parts(tmp_fd: int) -> list[tuple[str, os.stat_result]]:
    """Temporários do storage: arquivos comuns com o nome gerado, sem seguir symlink."""
    with os.scandir(tmp_fd) as scan:
        entries = list(scan)
    parts = []
    for entry in entries:
        if not _TMP_PART_NAME.fullmatch(entry.name):
            continue
        try:
            info = entry.stat(follow_symlinks=False)
        except FileNotFoundError:
            continue
        if S_ISREG(info.st_mode):
            parts.append((entry.name, info))
    return parts


def _unlink_temporary(name: str, tmp_fd: int) -> bool:
    try:
        os.unlink(name, dir_fd=tmp_fd)
    except FileNotFoundError:
        return True
    except OSError:
        logger.warning(
            "temporário não removido", exc_info=True, extra={"fields": {"tmp_part": name}}
        )
        return False
    return True


def _names_of(key: str) -> list[str]:
    if not _is_valid_key(key):
        raise InvalidStorageKeyError(key)
    return key.split("/")


def _is_valid_key(key: str) -> bool:
    if not key or len(key) > _MAX_KEY_LENGTH:
        return False
    return all(_KEY_SEGMENT.fullmatch(segment) for segment in key.split("/"))


def _write_exclusive(
    name: str, directory_fd: int, chunks: Iterable[bytes], max_bytes: int
) -> tuple[bytes, int]:
    digest = hashlib.sha256()
    size = 0
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    fd = os.open(name, flags, _FILE_MODE, dir_fd=directory_fd)
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
