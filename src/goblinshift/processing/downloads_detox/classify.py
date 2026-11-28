"""Classificação de uma entrada por assinatura, extensão e UTF-8 incremental (1.4).

Nada é decodificado nem aberto: imagens, PDF e ZIP são reconhecidos pelos primeiros bytes, só
no primeiro bloco lido, e texto por uma passada do decodificador UTF-8. A extensão tem de estar
entre as aceitas pela versão e ser coerente com a assinatura; incoerência é recusa, nunca
adivinhação (A11). ZIP é opaco: um JAR, APK, DOCX ou XLSX chamado `.zip` passa como ZIP, porque
só abrindo o arquivo daria para distingui-los. PDF também é opaco: JavaScript, anexos e
formulários só se veem abrindo, e isso é do PDF Press.
"""

import codecs
from collections.abc import Callable, Iterable
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
    DOCUMENT = "document"


class Format(StrEnum):
    JPEG = "jpeg"
    PNG = "png"
    CSV = "csv"
    TXT = "txt"
    ZIP = "zip"
    PDF = "pdf"
    WEBP = "webp"
    GIF = "gif"

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
    Format.PDF: Kind.DOCUMENT,
    Format.WEBP: Kind.IMAGE,
    Format.GIF: Kind.IMAGE,
}
_CANONICAL_EXTENSION = {
    Format.JPEG: ".jpg",
    Format.PNG: ".png",
    Format.CSV: ".csv",
    Format.TXT: ".txt",
    Format.ZIP: ".zip",
    Format.PDF: ".pdf",
    Format.WEBP: ".webp",
    Format.GIF: ".gif",
}
_FORMAT_BY_EXTENSION = {
    ".jpg": Format.JPEG,
    ".jpeg": Format.JPEG,
    ".png": Format.PNG,
    ".csv": Format.CSV,
    ".txt": Format.TXT,
    ".zip": Format.ZIP,
    # Versão 2: no fim, para a mensagem da v1 continuar idêntica.
    ".pdf": Format.PDF,
    ".webp": Format.WEBP,
    ".gif": Format.GIF,
}
# Formatos que uma versão da receita aceita; a v1 fica congelada nos cinco de 1.4.
type AcceptedFormats = frozenset[Format]
V1_FORMATS: AcceptedFormats = frozenset(
    {Format.JPEG, Format.PNG, Format.CSV, Format.TXT, Format.ZIP}
)
V2_FORMATS: AcceptedFormats = V1_FORMATS | {Format.PDF, Format.WEBP, Format.GIF}
_LABEL = {
    Format.JPEG: "JPEG",
    Format.PNG: "PNG",
    Format.ZIP: "ZIP",
    Format.PDF: "PDF",
    Format.WEBP: "WebP",
    Format.GIF: "GIF",
}
_RIFF = b"RIFF"
_WEBP_FORM = b"WEBP"
# O tipo da forma RIFF vem depois do tamanho do contêiner (4 bytes).
_RIFF_FORM_SLICE = slice(8, 12)


def _starts_with(signature: bytes) -> Callable[[bytes], bool]:
    return lambda head: head.startswith(signature)


def _is_webp(head: bytes) -> bool:
    # WAV e AVI também são RIFF: só a forma WEBP distingue.
    return head.startswith(_RIFF) and head[_RIFF_FORM_SLICE] == _WEBP_FORM


_BINARY_SIGNATURES: tuple[tuple[Callable[[bytes], bool], Format], ...] = (
    (_starts_with(b"\xff\xd8\xff"), Format.JPEG),
    (_starts_with(b"\x89PNG\r\n\x1a\n"), Format.PNG),
    (_starts_with(b"PK\x03\x04"), Format.ZIP),
    (_starts_with(b"PK\x05\x06"), Format.ZIP),  # ZIP vazio: só o fim do diretório central
    (_starts_with(b"%PDF-"), Format.PDF),
    (_starts_with(b"GIF87a"), Format.GIF),
    (_starts_with(b"GIF89a"), Format.GIF),
    (_is_webp, Format.WEBP),
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


def classify(source: BinaryIO, original_name: str, accepted: AcceptedFormats) -> Format | Refusal:
    """Formato da entrada, lida do início; o texto é lido até o fim, em blocos.

    Só valem a extensão e a assinatura dos formatos em `accepted`: o que a versão não aceita é
    recusado pela extensão, e a assinatura dele nem é reconhecida.
    """
    extension = extension_of(original_name)
    expected = _format_by_extension(extension, accepted)
    if expected is None:
        names = ", ".join(ext for ext, fmt in _FORMAT_BY_EXTENSION.items() if fmt in accepted)
        return Refusal(f"extensão {extension or '(nenhuma)'} não aceita; aceitas: {names}")
    head = source.read(TEXT_BLOCK_BYTES)
    if head.startswith(_EXECUTABLE_SIGNATURES):
        return Refusal("assinatura de executável ou script")
    found = _binary_format(head, accepted)
    if found is expected:
        return expected
    if found is not None:
        return Refusal(f"o conteúdo é {_LABEL[found]}, mas a extensão é {extension}")
    if expected in _LABEL:
        return Refusal(f"o conteúdo não tem a assinatura de {_LABEL[expected]}")
    refusal = _text_refusal(chain([head], iter(lambda: source.read(TEXT_BLOCK_BYTES), b"")))
    return expected if refusal is None else Refusal(refusal)


def _format_by_extension(extension: str, accepted: AcceptedFormats) -> Format | None:
    # Só ASCII: o casefold levaria um `s` longo (U+017F) de `.c\u017fv` a `.csv`.
    if not extension.isascii():
        return None
    fmt = _FORMAT_BY_EXTENSION.get(extension.casefold())
    return fmt if fmt in accepted else None


def _binary_format(head: bytes, accepted: AcceptedFormats) -> Format | None:
    return next(
        (fmt for matches, fmt in _BINARY_SIGNATURES if fmt in accepted and matches(head)),
        None,
    )


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
