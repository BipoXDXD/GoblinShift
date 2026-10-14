"""Escrita do ZIP determinístico (3.6, 3.9): mesmo conteúdo, mesmos bytes.

Nada do ambiente entra no arquivo: `date_time` fixo em 1980-01-01, permissão 0644 de arquivo
regular Unix, nenhuma entrada de diretório, ordem dada por quem chama, compressão por extensão
(`compression_for`) e nível de DEFLATE fixo. A saída é um arquivo comum (seekable), então o
`zipfile` volta para preencher tamanho e CRC no cabeçalho local e nunca usa data descriptor.

O determinismo entre máquinas depende ainda do zlib e do `zipfile` do interpretador: os dois vêm
da imagem do executor, fixada por digest. Outro zlib pode comprimir os mesmos bytes de outro jeito.
"""

import io
import os
import shutil
import stat
import zipfile
from collections.abc import Buffer, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from goblinshift.processing.recipe import FailureCode, RecipeFailure

MIB = 1024 * 1024
FIXED_DATE_TIME = (1980, 1, 1, 0, 0, 0)  # a menor data que o formato ZIP representa
DEFLATE_LEVEL = 6
COPY_CHUNK_BYTES = 1 * MIB
_REGULAR_FILE_0644 = (stat.S_IFREG | 0o644) << 16
_UNIX = 3  # "create_system" do ZIP: atributos externos no formato Unix
# Formatos que já chegam comprimidos: DEFLATE só gastaria CPU.
_STORED_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".zip"})


@dataclass(frozen=True)
class ZipSource:
    arcname: str  # caminho dentro do ZIP, o mesmo relativo a out/ ("images/foto.jpg")
    path: Path


def compression_for(arcname: str) -> int:
    """`ZIP_STORED` para .jpg, .jpeg, .png e .zip (qualquer caixa); `ZIP_DEFLATED` no resto."""
    suffix = PurePosixPath(arcname).suffix.casefold()
    return zipfile.ZIP_STORED if suffix in _STORED_SUFFIXES else zipfile.ZIP_DEFLATED


def write_zip(target: Path, sources: Sequence[ZipSource], max_bytes: int) -> None:
    """Cria `target` com criação exclusiva e as entradas na ordem de `sources`.

    Passar de `max_bytes` levanta `RecipeFailure(OUTPUT_LIMIT_EXCEEDED)` antes de o excesso
    chegar ao disco; o arquivo incompleto fica para quem chamou apagar.
    """
    with _CappedWriter(target, max_bytes) as raw, zipfile.ZipFile(raw, "w") as archive:
        for source in sources:
            _add(archive, source)


def open_regular(path: Path) -> BinaryIO:
    """Abre uma saída para leitura sem seguir symlink; o que não for arquivo regular é recusado."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    source = os.fdopen(fd, "rb")
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        source.close()
        raise OSError(f"saída não é arquivo regular: {path.name!r}")
    return source


def _add(archive: zipfile.ZipFile, source: ZipSource) -> None:
    with open_regular(source.path) as data:
        info = zipfile.ZipInfo(source.arcname, date_time=FIXED_DATE_TIME)
        info.create_system = _UNIX
        info.external_attr = _REGULAR_FILE_0644
        info.compress_type = compression_for(source.arcname)
        if info.compress_type == zipfile.ZIP_DEFLATED:
            info.compress_level = DEFLATE_LEVEL
        # Tamanho conhecido antes de abrir a entrada: o zipfile decide o ZIP64 por ele.
        info.file_size = os.fstat(data.fileno()).st_size
        with archive.open(info, "w") as entry:
            shutil.copyfileobj(data, entry, COPY_CHUNK_BYTES)


class _CappedWriter(io.BufferedWriter):
    """Arquivo novo e exclusivo que recusa crescer além do teto do pacote."""

    def __init__(self, path: Path, max_bytes: int) -> None:
        # "xb" abre com O_CREAT | O_EXCL: um pacote, ou symlink, já existente nunca é reescrito.
        super().__init__(io.FileIO(path, "xb"))
        self._max_bytes = max_bytes

    def write(self, data: Buffer, /) -> int:
        # O zipfile volta ao cabeçalho local para reescrevê-lo: o que conta é a posição final.
        end = self.tell() + memoryview(data).nbytes
        if end > self._max_bytes:
            raise RecipeFailure(
                FailureCode.OUTPUT_LIMIT_EXCEEDED,
                f"o pacote passaria de {self._max_bytes} bytes",
            )
        return super().write(data)
