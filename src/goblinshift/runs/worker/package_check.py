"""Conferência do pacote comum contra o que a coleta leu de out/ (ADR 019; docs/GoblinShiftPlan.md,
3.6).

`manifest.json` e `<recipe_id>.zip` vêm do sandbox e são publicados para o dono baixar: são dado
não confiável. O pacote só passa se disser exatamente o que out/ tem:

- o manifesto (schema 1) lista em `outputs`, na ordem, as saídas citadas no relatório, cada uma com
  o caminho, o tamanho e o SHA-256 que o worker calculou;
- o ZIP tem exatamente as saídas, o manifesto e o relatório, nessa ordem e com esses nomes. Como os
  nomes esperados são caminhos reais de out/, já conferidos pela coleta, a igualdade recusa
  entradas extras ou repetidas, caminho absoluto, `..` e diretório;
- cada entrada é arquivo regular 0644 (nada de symlink), não cifrada, com a compressão que o
  empacotador escolhe pela extensão, o tamanho declarado igual ao do arquivo de out/ e o conteúdo
  com o mesmo SHA-256. Nada é descomprimido além do tamanho esperado, que já cabe nos tetos de
  1.7: uma bomba de compressão não passa do primeiro arquivo, minta ou não o cabeçalho.

Esse é o contrato do `zip_writer` do executor; um ZIP de outro formato é executor fora do contrato.
"""

import hashlib
import os
import stat
import zipfile
import zlib
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol

from goblinshift.processing.packaging import MANIFEST_SCHEMA_VERSION, compression_for
from goblinshift.runs.worker.strict_json import parse_strict_json

READ_CHUNK_BYTES = 1024 * 1024
# O modo que o empacotador grava em toda entrada: arquivo regular Unix, 0644.
_REGULAR_FILE_0644 = stat.S_IFREG | 0o644
_UNIX = 3
# Bit 0: cifrada; bit 6: cifragem forte. O empacotador não cifra nada.
_ENCRYPTED_FLAGS = 0x1 | 0x40
# O que o zipfile levanta diante de um arquivo malformado (CRC, cabeçalho, fluxo DEFLATE).
_MALFORMED = (zipfile.BadZipFile, zipfile.LargeZipFile, zlib.error, EOFError, NotImplementedError)


class PackageError(Exception):
    """Manifesto ou ZIP fora do contrato do empacotador."""


class DigestedFile(Protocol):
    """Arquivo de out/ como a coleta o leu: caminho relativo, tamanho e SHA-256."""

    @property
    def name(self) -> str: ...

    @property
    def size(self) -> int: ...

    @property
    def sha256(self) -> bytes: ...


def verify_manifest(data: bytes, contents: Sequence[DigestedFile]) -> None:
    """O manifesto lista exatamente `contents`, na ordem, com tamanho e SHA-256."""
    try:
        document = parse_strict_json(data)
    except ValueError:
        raise PackageError("manifesto não é JSON aceito") from None
    if not isinstance(document, dict) or not _is_int(
        document.get("schema_version"), MANIFEST_SCHEMA_VERSION
    ):
        raise PackageError("manifesto com schema_version desconhecida")
    listed = document.get("outputs")
    expected = [(item.name, item.size, item.sha256.hex()) for item in contents]
    if not isinstance(listed, list) or [_listed(raw) for raw in listed] != expected:
        raise PackageError("manifesto não lista exatamente as saídas coletadas")


def verify_zip(path: Path, expected: Sequence[DigestedFile], heartbeat: Callable[[], None]) -> None:
    """O ZIP tem exatamente as entradas `expected`, na ordem, com o mesmo conteúdo.

    `heartbeat` é chamado a cada bloco lido, para quem chama renovar o lease.
    """
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as source:
        try:
            with zipfile.ZipFile(source) as archive:
                _verify_entries(archive, expected, heartbeat)
        except _MALFORMED as error:
            raise PackageError(f"pacote malformado: {type(error).__name__}") from None


def _verify_entries(
    archive: zipfile.ZipFile, expected: Sequence[DigestedFile], heartbeat: Callable[[], None]
) -> None:
    infos = archive.infolist()
    if [info.filename for info in infos] != [item.name for item in expected]:
        raise PackageError("pacote com entradas diferentes das saídas, manifesto e relatório")
    for info, item in zip(infos, expected, strict=True):
        _require_plain_entry(info, item)
        _require_same_content(archive, info, item, heartbeat)


def _require_plain_entry(info: zipfile.ZipInfo, item: DigestedFile) -> None:
    if info.create_system != _UNIX or info.external_attr >> 16 != _REGULAR_FILE_0644:
        raise PackageError(f"pacote com entrada que não é arquivo regular 0644: {item.name!r}")
    if info.flag_bits & _ENCRYPTED_FLAGS:
        raise PackageError(f"pacote com entrada cifrada: {item.name!r}")
    if info.compress_type != compression_for(item.name):
        raise PackageError(f"pacote com compressão fora do contrato: {item.name!r}")
    stored = info.compress_type == zipfile.ZIP_STORED
    if info.file_size != item.size or (stored and info.compress_size != info.file_size):
        raise PackageError(f"pacote com tamanho diferente do de out/: {item.name!r}")


def _require_same_content(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    item: DigestedFile,
    heartbeat: Callable[[], None],
) -> None:
    digest = hashlib.sha256()
    size = 0
    with archive.open(info) as entry:
        # O zipfile para no tamanho declarado, já igual ao esperado; a soma confere de novo.
        while size <= item.size and (chunk := entry.read(READ_CHUNK_BYTES)):
            heartbeat()
            size += len(chunk)
            digest.update(chunk)
    if size != item.size or digest.digest() != item.sha256:
        raise PackageError(f"pacote com conteúdo diferente do de out/: {item.name!r}")


def _listed(raw: object) -> tuple[object, object, object] | None:
    if not isinstance(raw, dict):
        return None
    size = raw.get("size")
    return (raw.get("path"), size if _is_int(size) else None, raw.get("sha256"))


def _is_int(raw: object, value: int | None = None) -> bool:
    """Inteiro de verdade (o `bool` do Python é `int`), opcionalmente igual a `value`."""
    if not isinstance(raw, int) or isinstance(raw, bool):
        return False
    return value is None or raw == value
