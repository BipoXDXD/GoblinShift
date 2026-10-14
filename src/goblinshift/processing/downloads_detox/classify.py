"""Classificação de uma entrada por assinatura, extensão e UTF-8 incremental (1.4).

Nada é decodificado nem aberto: imagens e ZIP são reconhecidos pelos primeiros bytes, e texto
por uma passada do decodificador UTF-8. A extensão tem de estar na lista e ser coerente com a
assinatura; incoerência é recusa, nunca adivinhação (A11). ZIP é opaco: um JAR, APK, DOCX ou
XLSX chamado `.zip` passa como ZIP, porque só abrindo o arquivo daria para distingui-los.
"""

import codecs
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from itertools import chain
from typing import BinaryIO

from goblinshift.processing.names import extension_of

# Blocos de leitura do texto; o primeiro também serve para as assinaturas.
TEXT_BLOCK_BYTES = 64 * 1024


class Kind(StrEnum):
    IMAGE = "image"
    TABLE = "table"
    TEXT = "text"
    ARCHIVE = "archive"


class Format(StrEnum):
    JPEG = "jpeg"
    PNG = "png"
    CSV = "csv"
    TXT = "txt"
    ZIP = "zip"

    @property
    def kind(self) -> Kind:
        return _KIND[self]

    @property
    def extension(self) -> str:
        """Extensão canônica, usada em `name_mode: indexed` (A17)."""
        return _CANONICAL_EXTENSION[self]


_KIND = {
    Format.JPEG: Kind.IMAGE,
    Format.PNG: Kind.IMAGE,
    Format.CSV: Kind.TABLE,
    Format.TXT: Kind.TEXT,
    Format.ZIP: Kind.ARCHIVE,
}
_CANONICAL_EXTENSION = {
    Format.JPEG: ".jpg",
    Format.PNG: ".png",
    Format.CSV: ".csv",
    Format.TXT: ".txt",
    Format.ZIP: ".zip",
}
_FORMAT_BY_EXTENSION = {
    ".jpg": Format.JPEG,
    ".jpeg": Format.JPEG,
    ".png": Format.PNG,
    ".csv": Format.CSV,
    ".txt": Format.TXT,
    ".zip": Format.ZIP,
}
_LABEL = {Format.JPEG: "JPEG", Format.PNG: "PNG", Format.ZIP: "ZIP"}
_BINARY_SIGNATURES = (
    (b"\xff\xd8\xff", Format.JPEG),
    (b"\x89PNG\r\n\x1a\n", Format.PNG),
    (b"PK\x03\x04", Format.ZIP),
    (b"PK\x05\x06", Format.ZIP),  # ZIP vazio: só o fim do diretório central
)
_EXECUTABLE_SIGNATURES = (
    b"MZ",  # PE (Windows)
    b"\x7fELF",
    b"\xfe\xed\xfa\xce",  # Mach-O 32 e 64 bits, nas duas ordens de bytes
    b"\xfe\xed\xfa\xcf",
    b"\xce\xfa\xed\xfe",
    b"\xcf\xfa\xed\xfe",
    b"\xca\xfe\xba\xbe",  # Mach-O universal (é também a classe Java)
    b"#!",  # script com interpretador
)
_MARKUP_OPENINGS = (
    "<!doctype html",
    "<html",
    "<head",
    "<body",
    "<script",
    "<svg",
    # XML genérico: sem analisar o documento não dá para separar SVG (A20).
    "<?xml",
)
_MARKUP_CHARS = max(len(opening) for opening in _MARKUP_OPENINGS)
_BOM = "\ufeff"
_NUL = "\x00"


@dataclass(frozen=True)
class Refusal:
    """Entrada recusada com `UNSUPPORTED_FORMAT`; `reason` vai para o relatório."""

    reason: str


def classify(source: BinaryIO, original_name: str) -> Format | Refusal:
    """Formato da entrada, lida do início; o texto é lido até o fim, em blocos."""
    extension = extension_of(original_name)
    expected = _format_by_extension(extension)
    if expected is None:
        accepted = ", ".join(_FORMAT_BY_EXTENSION)
        return Refusal(f"extensão {extension or '(nenhuma)'} não aceita; aceitas: {accepted}")
    head = source.read(TEXT_BLOCK_BYTES)
    if head.startswith(_EXECUTABLE_SIGNATURES):
        return Refusal("assinatura de executável ou script")
    found = _binary_format(head)
    if found is expected:
        return expected
    if found is not None:
        return Refusal(f"o conteúdo é {_LABEL[found]}, mas a extensão é {extension}")
    if expected in _LABEL:
        return Refusal(f"o conteúdo não tem a assinatura de {_LABEL[expected]}")
    refusal = _text_refusal(chain([head], iter(lambda: source.read(TEXT_BLOCK_BYTES), b"")))
    return expected if refusal is None else Refusal(refusal)


def _format_by_extension(extension: str) -> Format | None:
    # Só ASCII: o casefold levaria um `s` longo (U+017F) de `.c\u017fv` a `.csv`.
    if not extension.isascii():
        return None
    return _FORMAT_BY_EXTENSION.get(extension.casefold())


def _binary_format(head: bytes) -> Format | None:
    return next((fmt for signature, fmt in _BINARY_SIGNATURES if head.startswith(signature)), None)


def _text_refusal(blocks: Iterable[bytes]) -> str | None:
    """Motivo para recusar o texto, ou None se ele é UTF-8 sem NUL e sem marcação reconhecível."""
    decoder = codecs.getincrementaldecoder("utf-8")()
    opening = _Opening()
    try:
        for block in blocks:
            text = decoder.decode(block)
            if _NUL in text:
                return "o texto contém o caractere NUL"
            opening.feed(text)
    except UnicodeDecodeError:
        return "o conteúdo não é texto UTF-8 válido"
    try:
        decoder.decode(b"", final=True)
    except UnicodeDecodeError:
        return "o texto termina no meio de um caractere UTF-8"
    if opening.is_markup():
        return "o texto começa como HTML, SVG ou XML"
    return None


class _Opening:
    """Primeiros caracteres do texto depois do BOM e dos espaços, o bastante para a marcação."""

    def __init__(self) -> None:
        self._text = ""
        self._at_start = True

    def feed(self, text: str) -> None:
        if len(self._text) >= _MARKUP_CHARS or not text:
            return
        if self._at_start:
            text = text.removeprefix(_BOM)
            self._at_start = False
        self._text = (self._text + text).lstrip()[:_MARKUP_CHARS]

    def is_markup(self) -> bool:
        return self._text.casefold().startswith(_MARKUP_OPENINGS)
