"""Parâmetros do Image Forge v2 (docs/pesquisa/concorrencia-imagem-e-midia.md, §2).

Superconjunto da v1: os mesmos campos e defaults, mais o modo de ajuste, a escala e o WebP. Sem
o PIL, para o catálogo da API poder importar. Como na v1, `ParamsV2` nasce válido também na
combinação, sempre comparando com o default (a forma resolvida pelo catálogo traz todos eles):
- `scale_percent` só vale com `fit=scale`, e a caixa (`max_width`/`max_height`) só sem ele;
- opções de JPEG não valem quando nenhuma saída é JPEG (`png`, `webp`);
- opções de WebP não valem quando nenhuma saída é WebP (`jpeg`, `png`);
- `webp_quality` não vale com `webp_lossless`.

O `output_format` tem enum próprio: o `OutputFormat` da v1 fica congelado (1.6).
"""

from collections.abc import Mapping
from dataclasses import dataclass, fields
from enum import StrEnum

from goblinshift.processing.image_forge.geometry import FitMode
from goblinshift.processing.image_forge.params import (
    DEFAULT_JPEG_QUALITY,
    DEFAULT_SIDE,
    MAX_JPEG_QUALITY,
    MAX_SIDE,
    MIN_JPEG_QUALITY,
    MIN_SIDE,
    WHITE,
    HexColor,
    InvalidParametersError,
)

MIN_WEBP_QUALITY = 60
MAX_WEBP_QUALITY = 95
DEFAULT_WEBP_QUALITY = 80
MIN_SCALE_PERCENT = 1
MAX_SCALE_PERCENT = 100
DEFAULT_SCALE_PERCENT = 100


class OutputFormatV2(StrEnum):
    PRESERVE = "preserve"  # PNG e GIF saem PNG; JPEG e MPO saem JPEG; WebP sai WebP
    JPEG = "jpeg"
    PNG = "png"
    WEBP = "webp"


# Formatos de saída em que cada grupo de opções não tem efeito.
_WITHOUT_JPEG = frozenset({OutputFormatV2.PNG, OutputFormatV2.WEBP})
_WITHOUT_WEBP = frozenset({OutputFormatV2.JPEG, OutputFormatV2.PNG})


@dataclass(frozen=True)
class ParamsV2:
    max_width: int = DEFAULT_SIDE
    max_height: int = DEFAULT_SIDE
    output_format: OutputFormatV2 = OutputFormatV2.PRESERVE
    jpeg_quality: int = DEFAULT_JPEG_QUALITY  # só nas saídas JPEG
    jpeg_background: HexColor = WHITE  # fundo da transparência e da margem nas saídas JPEG
    make_thumbnails: bool = False
    fit: FitMode = FitMode.CONTAIN
    scale_percent: int = DEFAULT_SCALE_PERCENT  # só com `fit=scale`
    webp_quality: int = DEFAULT_WEBP_QUALITY  # só nas saídas WebP com perda
    webp_lossless: bool = False  # só nas saídas WebP

    def __post_init__(self) -> None:
        _require_range("max_width", self.max_width, MIN_SIDE, MAX_SIDE)
        _require_range("max_height", self.max_height, MIN_SIDE, MAX_SIDE)
        _require_range("jpeg_quality", self.jpeg_quality, MIN_JPEG_QUALITY, MAX_JPEG_QUALITY)
        _require_range("webp_quality", self.webp_quality, MIN_WEBP_QUALITY, MAX_WEBP_QUALITY)
        _require_range("scale_percent", self.scale_percent, MIN_SCALE_PERCENT, MAX_SCALE_PERCENT)
        if self.fit is FitMode.SCALE:
            self._require_default(
                "fit é scale", ("max_width", DEFAULT_SIDE), ("max_height", DEFAULT_SIDE)
            )
        else:
            self._require_default(
                f"fit é {self.fit.value}", ("scale_percent", DEFAULT_SCALE_PERCENT)
            )
        if self.output_format in _WITHOUT_JPEG:
            self._require_default(
                f"output_format é {self.output_format.value}",
                ("jpeg_quality", DEFAULT_JPEG_QUALITY),
                ("jpeg_background", WHITE),
            )
        if self.output_format in _WITHOUT_WEBP:
            self._require_default(
                f"output_format é {self.output_format.value}",
                ("webp_quality", DEFAULT_WEBP_QUALITY),
                ("webp_lossless", False),
            )
        if self.webp_lossless:
            self._require_default("webp_lossless é true", ("webp_quality", DEFAULT_WEBP_QUALITY))

    def _require_default(self, reason: str, *options: tuple[str, object]) -> None:
        changed = [name for name, default in options if getattr(self, name) != default]
        if changed:
            raise InvalidParametersError(f"{', '.join(changed)} não vale quando {reason}")


def params_v2_from_mapping(raw: Mapping[str, object]) -> ParamsV2:
    """Converte a configuração decodificada de JSON; recusa campos desconhecidos e tipos errados."""
    unknown = sorted(set(raw) - _KNOWN_OPTIONS)
    if unknown:
        raise InvalidParametersError(f"opções desconhecidas: {unknown}")
    return ParamsV2(
        max_width=_integer(raw.get("max_width", DEFAULT_SIDE), "max_width"),
        max_height=_integer(raw.get("max_height", DEFAULT_SIDE), "max_height"),
        output_format=_choice(
            raw.get("output_format", OutputFormatV2.PRESERVE.value), "output_format", OutputFormatV2
        ),
        jpeg_quality=_integer(raw.get("jpeg_quality", DEFAULT_JPEG_QUALITY), "jpeg_quality"),
        jpeg_background=HexColor.parse(
            _string(raw.get("jpeg_background", str(WHITE)), "jpeg_background")
        ),
        make_thumbnails=_boolean(raw.get("make_thumbnails", False), "make_thumbnails"),
        fit=_choice(raw.get("fit", FitMode.CONTAIN.value), "fit", FitMode),
        scale_percent=_integer(raw.get("scale_percent", DEFAULT_SCALE_PERCENT), "scale_percent"),
        webp_quality=_integer(raw.get("webp_quality", DEFAULT_WEBP_QUALITY), "webp_quality"),
        webp_lossless=_boolean(raw.get("webp_lossless", False), "webp_lossless"),
    )


_KNOWN_OPTIONS = frozenset(field.name for field in fields(ParamsV2))


def _require_range(option: str, value: int, minimum: int, maximum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidParametersError(f"{option} deve ser inteiro")
    if not minimum <= value <= maximum:
        raise InvalidParametersError(f"{option} deve estar entre {minimum} e {maximum}")


def _choice[E: StrEnum](raw: object, option: str, enum: type[E]) -> E:
    try:
        return enum(_string(raw, option))
    except ValueError:
        allowed = [member.value for member in enum]
        raise InvalidParametersError(f"{option} deve ser um de {allowed}") from None


def _integer(raw: object, option: str) -> int:
    # bool é subclasse de int em Python; `true` do JSON não é um tamanho.
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise InvalidParametersError(f"{option} deve ser inteiro")
    return raw


def _boolean(raw: object, option: str) -> bool:
    if not isinstance(raw, bool):
        raise InvalidParametersError(f"{option} deve ser booleano")
    return raw


def _string(raw: object, option: str) -> str:
    if not isinstance(raw, str):
        raise InvalidParametersError(f"{option} deve ser texto")
    return raw
