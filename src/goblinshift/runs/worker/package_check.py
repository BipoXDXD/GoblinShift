"""Conferência do pacote comum contra o que a coleta leu de out/ (ADR 019; docs/GoblinShiftPlan.md,
3.6).

`manifest.json` e `<recipe_id>.zip` vêm do sandbox e são publicados para o dono baixar: são dado
não confiável. O pacote só passa se disser exatamente o que out/ tem:

- o manifesto (schema 1) lista em `outputs`, na ordem, as saídas citadas no relatório, cada uma com
  o caminho, o tamanho e o SHA-256 que o worker calculou; em `inputs`, uma entrada por item do
  relatório, com a mesma posição, o mesmo desfecho (status, saídas, código do catálogo,
  duplicata) e só as chaves do schema; em `recipe`, a receita da Run. Os textos (nome original,
  motivo, avisos) são da receita, como no `report.json`; parâmetros, versão, imagem e digests das
  entradas não são conferidos aqui;
- o ZIP tem exatamente as saídas, o manifesto e o relatório, nessa ordem e com esses nomes. Como os
  nomes esperados são caminhos reais de out/, já conferidos pela coleta, a igualdade recusa
  entradas extras ou repetidas, caminho absoluto, `..` e diretório;
- cada entrada é arquivo regular 0644 (nada de symlink), não cifrada, com a compressão que o
  empacotador escolhe pela extensão, o tamanho declarado igual ao do arquivo de out/ e o conteúdo
  com o mesmo SHA-256. Nada é descomprimido além do tamanho esperado, que já cabe nos tetos de
  1.7: uma bomba de compressão não passa do primeiro arquivo, minta ou não o cabeçalho.

Antes de o `zipfile` abrir o arquivo, a estrutura é lida à mão e precisa ser a canônica do
empacotador, para que nenhum leitor (o `zipfile`, um unzip que segue os cabeçalhos locais, o do
sistema do usuário) veja outra coisa: entradas contíguas desde o byte 0, diretório central logo
depois delas e o fim do diretório (EOCD) nos últimos 22 bytes, sem comentário, sem ZIP64 e sem
campos extras; cada cabeçalho local igual à sua entrada do diretório central (flags, método, CRC,
tamanhos, nome); nome em bytes exatamente UTF-8 do esperado, com a flag UTF-8 só quando não é
ASCII (o `zipfile` corta o nome no NUL e decodifica CP437 sem a flag). O diretório só é lido se
tiver a quantidade esperada de entradas e um tamanho compatível com ela: o `zipfile` o carrega
inteiro na memória, e um ZIP de 512 MiB caberia milhões de entradas.
"""

import hashlib
import os
import stat
import struct
import zipfile
import zlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Protocol

from goblinshift.processing.output_dir import MAX_PATH_BYTES
from goblinshift.processing.packaging import MANIFEST_SCHEMA_VERSION, compression_for
from goblinshift.runs.worker.strict_json import parse_strict_json

READ_CHUNK_BYTES = 1024 * 1024
# O modo que o empacotador grava em toda entrada: arquivo regular Unix, 0644.
_REGULAR_FILE_0644 = stat.S_IFREG | 0o644
_UNIX = 3
# Única flag que o empacotador grava: nome em UTF-8 (bit 11), só para nome não ASCII. Qualquer
# outra (cifra, data descriptor, nível de compressão) é estrutura fora do contrato.
_UTF8_NAME = 0x800
# Fim do diretório central (EOCD), cabeçalho local e entrada do diretório central (APPNOTE 4.3).
_END_RECORD = struct.Struct("<4s4H2LH")
_END_SIGNATURE = b"PK\x05\x06"
_LOCAL_HEADER = struct.Struct("<4s5H3L2H")
_LOCAL_SIGNATURE = b"PK\x03\x04"
_CENTRAL_HEADER = struct.Struct("<4s6H3L5H2L")
_CENTRAL_SIGNATURE = b"PK\x01\x02"
# Uma entrada do diretório central: os 46 bytes fixos e o nome, sem extras nem comentário.
_MAX_CENTRAL_ENTRY_BYTES = _CENTRAL_HEADER.size + MAX_PATH_BYTES
# O que o zipfile levanta diante de um arquivo malformado (CRC, cabeçalho, fluxo DEFLATE, nome
# com a flag UTF-8 e bytes inválidos). A leitura canônica já recusa os conhecidos; isto é a rede.
_MALFORMED = (
    zipfile.BadZipFile,
    zipfile.LargeZipFile,
    zlib.error,
    EOFError,
    NotImplementedError,
    UnicodeDecodeError,
)
# Chaves do manifesto de schema 1 (`processing/packaging/manifest.py`).
_MANIFEST_KEYS = {"schema_version", "recipe", "parameters", "inputs", "outputs", "warnings"}
_RECIPE_KEYS = {"id", "version", "executor_image"}
_OUTPUT_KEYS = {"path", "size", "sha256", "source_position"}
_INPUT_COMMON_KEYS = {"position", "original_name", "size", "sha256", "status"}
_INPUT_KEYS = {
    "ok": _INPUT_COMMON_KEYS | {"outputs", "warnings"},
    "rejected": _INPUT_COMMON_KEYS | {"code", "reason"},
    "duplicate": _INPUT_COMMON_KEYS | {"duplicate_of"},
}


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


@dataclass(frozen=True)
class ManifestInput:
    """O desfecho de uma entrada como o relatório já conferido o dá."""

    position: int
    status: str  # "ok", "rejected" ou "duplicate"
    outputs: tuple[str, ...]
    code: str | None
    duplicate_of: int | None


def verify_manifest(
    data: bytes,
    contents: Sequence[DigestedFile],
    inputs: Sequence[ManifestInput],
    recipe_id: str,
) -> None:
    """O manifesto lista exatamente `contents` e `inputs`, na ordem, para a receita da Run."""
    try:
        document = parse_strict_json(data)
    except ValueError:
        raise PackageError("manifesto não é JSON aceito") from None
    if not isinstance(document, dict) or not _is_int(
        document.get("schema_version"), MANIFEST_SCHEMA_VERSION
    ):
        raise PackageError("manifesto com schema_version desconhecida")
    if document.keys() != _MANIFEST_KEYS or not _is_recipe(document["recipe"], recipe_id):
        raise PackageError("manifesto com chaves ou receita fora do schema 1")
    listed = document["outputs"]
    expected = [(item.name, item.size, item.sha256.hex()) for item in contents]
    if not isinstance(listed, list) or [_listed(raw) for raw in listed] != expected:
        raise PackageError("manifesto não lista exatamente as saídas coletadas")
    listed_inputs = document["inputs"]
    expected_inputs = [_input_fields(item) for item in inputs]
    if (
        not isinstance(listed_inputs, list)
        or [_listed_input(raw) for raw in listed_inputs] != expected_inputs
    ):
        raise PackageError("manifesto não lista as entradas como o relatório")


def _is_recipe(raw: object, recipe_id: str) -> bool:
    return isinstance(raw, dict) and raw.keys() == _RECIPE_KEYS and raw["id"] == recipe_id


def _input_fields(item: ManifestInput) -> tuple[object, ...]:
    return (item.position, item.status, item.outputs, item.code, item.duplicate_of)


def _listed_input(raw: object) -> tuple[object, ...] | None:
    if not isinstance(raw, dict):
        return None
    status = raw.get("status")
    keys = _INPUT_KEYS.get(status) if isinstance(status, str) else None
    outputs = raw.get("outputs", [])
    if keys is None or raw.keys() != keys or not isinstance(outputs, list):
        return None
    position, duplicate_of = raw["position"], raw.get("duplicate_of")
    return (
        position if _is_int(position) else None,
        status,
        tuple(outputs),
        raw.get("code"),
        duplicate_of if _is_int(duplicate_of) else None,
    )


def verify_zip(path: Path, expected: Sequence[DigestedFile], heartbeat: Callable[[], None]) -> None:
    """O ZIP tem exatamente as entradas `expected`, na ordem, com o mesmo conteúdo.

    `heartbeat` é chamado a cada bloco lido, para quem chama renovar o lease.
    """
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as source:
        _require_canonical_layout(source, expected)
        try:
            with zipfile.ZipFile(source) as archive:
                _verify_entries(archive, expected, heartbeat)
        except _MALFORMED as error:
            raise PackageError(f"pacote malformado: {type(error).__name__}") from None


@dataclass(frozen=True)
class _Entry:
    """Campos de uma entrada que precisam ser iguais no cabeçalho local e no diretório central."""

    flags: int
    method: int
    crc: int
    compressed_size: int
    size: int
    name: bytes


def _require_canonical_layout(source: BinaryIO, expected: Sequence[DigestedFile]) -> None:
    size = source.seek(0, os.SEEK_END)
    directory_at, directory = _central_directory(source, size, len(expected))
    entries = _central_entries(directory, expected)
    offset = 0
    for central, local_at in entries:
        if local_at != offset:
            raise PackageError("pacote com dados fora das entradas")
        offset = local_at + _require_same_local_header(source, local_at, central)
    if offset != directory_at:
        raise PackageError("pacote com dados fora das entradas")


def _central_directory(source: BinaryIO, size: int, entries: int) -> tuple[int, bytes]:
    """Posição e bytes do diretório central, com o EOCD nos últimos 22 bytes e sem comentário."""
    end_at = size - _END_RECORD.size
    if end_at < 0:
        raise PackageError("pacote sem fim de diretório central")
    source.seek(end_at)
    signature, disk, directory_disk, disk_entries, total, directory_size, directory_at, comment = (
        _END_RECORD.unpack(source.read(_END_RECORD.size))
    )
    if signature != _END_SIGNATURE or comment != 0:
        raise PackageError("pacote sem fim de diretório central nos últimos bytes")
    if (disk, directory_disk, disk_entries, total) != (0, 0, entries, entries):
        raise PackageError(f"pacote com {total} entradas no diretório; eram {entries}")
    if directory_size > entries * _MAX_CENTRAL_ENTRY_BYTES:
        raise PackageError(f"pacote com diretório central de {directory_size} bytes")
    if directory_at + directory_size != end_at:
        raise PackageError("pacote com dados entre o diretório central e o fim")
    source.seek(directory_at)
    return directory_at, source.read(directory_size)


def _central_entries(
    directory: bytes, expected: Sequence[DigestedFile]
) -> list[tuple[_Entry, int]]:
    """Cada entrada do diretório central e a posição do seu cabeçalho local."""
    entries = []
    at = 0
    for item in expected:
        if len(directory) - at < _CENTRAL_HEADER.size:
            raise PackageError("pacote com diretório central truncado")
        (signature, _made_by, _version, flags, method, _time, _date, crc, compressed, size,
         name_length, extra_length, comment_length, _disk, _internal, _external, local_at) = (
            _CENTRAL_HEADER.unpack_from(directory, at)
        )  # fmt: skip
        name_at = at + _CENTRAL_HEADER.size
        name = directory[name_at : name_at + name_length]
        if signature != _CENTRAL_SIGNATURE or extra_length or comment_length:
            raise PackageError("pacote com entrada do diretório central fora do contrato")
        _require_exact_name(name, flags, item)
        entries.append((_Entry(flags, method, crc, compressed, size, name), local_at))
        at = name_at + name_length
    if at != len(directory):
        raise PackageError("pacote com dados no diretório central além das entradas")
    return entries


def _require_exact_name(raw: bytes, flags: int, item: DigestedFile) -> None:
    encoded = item.name.encode()
    utf8_flag = 0 if item.name.isascii() else _UTF8_NAME
    if raw != encoded or flags != utf8_flag:
        raise PackageError(f"pacote com nome ou flags fora do contrato: {item.name!r}")


def _require_same_local_header(source: BinaryIO, at: int, central: _Entry) -> int:
    """Confere o cabeçalho local contra o central; devolve o tamanho da entrada com os dados."""
    source.seek(at)
    header = source.read(_LOCAL_HEADER.size)
    if len(header) != _LOCAL_HEADER.size:
        raise PackageError("pacote com cabeçalho local truncado")
    (signature, _version, flags, method, _time, _date, crc, compressed, size, name_length,
     extra_length) = _LOCAL_HEADER.unpack(header)  # fmt: skip
    name = source.read(name_length)
    local = _Entry(flags, method, crc, compressed, size, name)
    if signature != _LOCAL_SIGNATURE or extra_length or local != central:
        raise PackageError("pacote com cabeçalho local diferente do diretório central")
    return _LOCAL_HEADER.size + len(local.name) + local.compressed_size


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
    if not isinstance(raw, dict) or raw.keys() != _OUTPUT_KEYS:
        return None
    size = raw.get("size")
    return (raw.get("path"), size if _is_int(size) else None, raw.get("sha256"))


def _is_int(raw: object, value: int | None = None) -> bool:
    """Inteiro de verdade (o `bool` do Python é `int`), opcionalmente igual a `value`."""
    if not isinstance(raw, int) or isinstance(raw, bool):
        return False
    return value is None or raw == value
