"""Comparação antes/depois de um item, montada só com o que a receita já mede.

A receita escreve `counts` e `details` no `report.json`, e o executor é não confiável: o que chega
aqui é tratado como entrada externa. `comparison_from_json` é a única validação, e o resultado só
carrega inteiros e valores de listas fechadas (`StrEnum`), nunca nome de arquivo, nome de coluna
ou texto livre. O que foge da forma vira `None` (sem comparação), e o item segue como antes.

Os enums são próprios da comparação, não os parâmetros das receitas: a v2 das receitas acrescenta
formatos e congela os da v1, e o contrato da comparação não deve depender disso. Os testes conferem
que eles cobrem os valores da v1.

Downloads Detox não tem comparação: os bytes são preservados e o que muda é o nome.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, fields
from enum import StrEnum
from typing import ClassVar

from goblinshift.processing import image_forge, media_mill, pdf_press
from goblinshift.processing.csv_janitor import recipe as csv_janitor

KIND_TABLE, KIND_PDF, KIND_IMAGE, KIND_MEDIA = "table", "pdf", "image", "media"


class ImageFormat(StrEnum):
    JPEG = "jpeg"
    PNG = "png"
    MPO = "mpo"
    WEBP = "webp"  # só sai pelo Image Forge v2


class AudioCodec(StrEnum):
    """Codecs de áudio de origem aceitos pelo Media Mill (`media_mill.probe`)."""

    AAC = "aac"
    PCM_S16LE = "pcm_s16le"
    PCM_S16BE = "pcm_s16be"
    PCM_S24LE = "pcm_s24le"
    PCM_S24BE = "pcm_s24be"
    OPUS = "opus"
    VORBIS = "vorbis"


class AudioFormat(StrEnum):
    """Formato do áudio extraído pelo Media Mill."""

    WAV = "wav"
    FLAC = "flac"


@dataclass(frozen=True)
class TableComparison:
    kind: ClassVar[str] = KIND_TABLE

    records_before: int
    records_after: int


@dataclass(frozen=True)
class PdfComparison:
    kind: ClassVar[str] = KIND_PDF

    pages_before: int
    pages_after: int


@dataclass(frozen=True)
class ImageComparison:
    kind: ClassVar[str] = KIND_IMAGE

    width_before: int
    height_before: int
    width_after: int
    height_after: int
    format_before: ImageFormat
    format_after: ImageFormat
    bytes_before: int
    # Tamanho da saída, medido pelo worker na coleta; o relatório da receita não o traz.
    bytes_after: int | None = None


@dataclass(frozen=True)
class MediaComparison:
    """Vídeo → miniatura e áudio de origem → extraído; cada par vem inteiro ou fica `None`."""

    kind: ClassVar[str] = KIND_MEDIA

    width_before: int
    height_before: int
    width_after: int | None
    height_after: int | None
    audio_before: AudioCodec | None
    audio_after: AudioFormat | None


type ItemComparison = TableComparison | PdfComparison | ImageComparison | MediaComparison


def comparison_as_json(comparison: ItemComparison) -> dict[str, object]:
    """Forma guardada no banco: `kind` mais os campos, com os enums como `.value`."""
    raw: dict[str, object] = {"kind": comparison.kind}
    for field in fields(comparison):
        value = getattr(comparison, field.name)
        raw[field.name] = value.value if isinstance(value, StrEnum) else value
    return raw


def comparison_from_json(raw: object) -> ItemComparison | None:
    """Fronteira de confiança do JSON do executor ou do banco: o que foge da forma vira `None`."""
    if not isinstance(raw, dict):
        return None
    kind = raw.get("kind")
    if not isinstance(kind, str) or kind not in _PARSERS:
        return None
    cls, parse = _PARSERS[kind]
    if raw.keys() != {"kind", *(field.name for field in fields(cls))}:
        return None
    try:
        return parse(_Fields(raw))
    except _InvalidFieldError:
        return None


def comparison_from_report(
    recipe_id: str, counts: Mapping[str, int], details: Mapping[str, object]
) -> ItemComparison | None:
    """Traduz as chaves da receita para a forma de `comparison_as_json` e a valida."""
    candidate = _CANDIDATES.get(recipe_id)
    return None if candidate is None else comparison_from_json(candidate(counts, details))


class _InvalidFieldError(Exception):
    pass


class _Fields:
    def __init__(self, raw: Mapping[str, object]) -> None:
        self._raw = raw

    def count(self, key: str) -> int:
        value = self._raw[key]
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise _InvalidFieldError
        return value

    def dimension(self, key: str) -> int:
        value = self.count(key)
        if value < 1:
            raise _InvalidFieldError
        return value

    def choice[E: StrEnum](self, key: str, enum: type[E]) -> E:
        value = self._raw[key]
        if not isinstance(value, str):
            raise _InvalidFieldError
        try:
            return enum(value)
        except ValueError:
            raise _InvalidFieldError from None

    def absent(self, key: str) -> bool:
        return self._raw[key] is None


def _table(raw: _Fields) -> TableComparison:
    return TableComparison(raw.count("records_before"), raw.count("records_after"))


def _pdf(raw: _Fields) -> PdfComparison:
    return PdfComparison(raw.count("pages_before"), raw.count("pages_after"))


def _image(raw: _Fields) -> ImageComparison:
    return ImageComparison(
        width_before=raw.dimension("width_before"),
        height_before=raw.dimension("height_before"),
        width_after=raw.dimension("width_after"),
        height_after=raw.dimension("height_after"),
        format_before=raw.choice("format_before", ImageFormat),
        format_after=raw.choice("format_after", ImageFormat),
        bytes_before=raw.count("bytes_before"),
        bytes_after=None if raw.absent("bytes_after") else raw.count("bytes_after"),
    )


def _media(raw: _Fields) -> MediaComparison:
    has_thumbnail = _pair_present(raw, "width_after", "height_after")
    has_audio = _pair_present(raw, "audio_before", "audio_after")
    if not (has_thumbnail or has_audio):
        raise _InvalidFieldError
    return MediaComparison(
        width_before=raw.dimension("width_before"),
        height_before=raw.dimension("height_before"),
        width_after=raw.dimension("width_after") if has_thumbnail else None,
        height_after=raw.dimension("height_after") if has_thumbnail else None,
        audio_before=raw.choice("audio_before", AudioCodec) if has_audio else None,
        audio_after=raw.choice("audio_after", AudioFormat) if has_audio else None,
    )


def _pair_present(raw: _Fields, first: str, second: str) -> bool:
    """Os dois presentes ou os dois ausentes; um só é campo pela metade."""
    if raw.absent(first) != raw.absent(second):
        raise _InvalidFieldError
    return not raw.absent(first)


type _Parser = Callable[[_Fields], ItemComparison]

_PARSERS: dict[str, tuple[type[ItemComparison], _Parser]] = {
    KIND_TABLE: (TableComparison, _table),
    KIND_PDF: (PdfComparison, _pdf),
    KIND_IMAGE: (ImageComparison, _image),
    KIND_MEDIA: (MediaComparison, _media),
}


def _csv_candidate(counts: Mapping[str, int], _details: Mapping[str, object]) -> dict[str, object]:
    return {
        "kind": KIND_TABLE,
        "records_before": counts.get("records_read"),
        "records_after": counts.get("written"),
    }


def _pdf_candidate(counts: Mapping[str, int], _details: Mapping[str, object]) -> dict[str, object]:
    return {
        "kind": KIND_PDF,
        "pages_before": counts.get("pages"),
        "pages_after": counts.get("output_pages"),
    }


# As chaves de `details` do Image Forge têm o mesmo nome dos campos da comparação.
_IMAGE_KEYS = (
    "width_before",
    "height_before",
    "width_after",
    "height_after",
    "format_before",
    "format_after",
    "bytes_before",
)


def _image_candidate(
    _counts: Mapping[str, int], details: Mapping[str, object]
) -> dict[str, object]:
    return {
        "kind": KIND_IMAGE,
        **{key: details.get(key) for key in _IMAGE_KEYS},
        # Vem do tamanho da saída, medido na coleta; o relatório da receita não o traz.
        "bytes_after": None,
    }


def _media_candidate(
    _counts: Mapping[str, int], details: Mapping[str, object]
) -> dict[str, object]:
    # Sem miniatura (`thumbnail_count=0`) ou sem áudio, as chaves faltam e o par entra como None.
    return {
        "kind": KIND_MEDIA,
        "width_before": details.get("width"),
        "height_before": details.get("height"),
        "width_after": details.get("thumbnail_width"),
        "height_after": details.get("thumbnail_height"),
        "audio_before": details.get("audio_codec"),
        "audio_after": details.get("audio_format"),
    }


type _Candidate = Callable[[Mapping[str, int], Mapping[str, object]], dict[str, object]]

_CANDIDATES: dict[str, _Candidate] = {
    csv_janitor.RECIPE_ID: _csv_candidate,
    pdf_press.RECIPE_ID: _pdf_candidate,
    image_forge.RECIPE_ID: _image_candidate,
    media_mill.RECIPE_ID: _media_candidate,
}
