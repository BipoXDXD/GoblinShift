"""Parâmetros do Image Forge (docs/GoblinShiftPlan.md, 1.2).

`Params` nasce válido, inclusive na combinação (1.1: "recusar combinações incompatíveis"): com
`output_format: png` nenhuma saída é JPEG, então `jpeg_quality` e `jpeg_background` fora do default
são recusados. A regra compara com o default, e não com "o cliente enviou o campo", porque a forma
resolvida pelo catálogo sempre traz os defaults: repetir uma execução PNG com os parâmetros
registrados continua válido.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, fields
from enum import StrEnum
from typing import ClassVar, Self

MIN_SIDE = 16
MAX_SIDE = 4096
DEFAULT_SIDE = 1600
MIN_JPEG_QUALITY = 60
MAX_JPEG_QUALITY = 95
DEFAULT_JPEG_QUALITY = 85
MAX_CHANNEL = 255


class InvalidParametersError(ValueError):
    """Configuração recusada antes de ler qualquer imagem."""


class OutputFormat(StrEnum):
    PRESERVE = "preserve"  # JPEG e MPO saem JPEG; PNG sai PNG
    JPEG = "jpeg"
    PNG = "png"


@dataclass(frozen=True)
class HexColor:
    """Cor `#RRGGBB`; o texto canônico é em maiúsculas."""

    red: int
    green: int
    blue: int

    _PATTERN: ClassVar[re.Pattern[str]] = re.compile(r"#[0-9A-Fa-f]{6}", re.ASCII)

    def __post_init__(self) -> None:
        if not all(0 <= channel <= MAX_CHANNEL for channel in self.rgb):
            raise InvalidParametersError(f"jpeg_background fora de 0 a 255: {self.rgb}")

    @classmethod
    def parse(cls, text: str) -> Self:
        if not cls._PATTERN.fullmatch(text):
            raise InvalidParametersError(f"jpeg_background deve ser #RRGGBB, recebido {text!r}")
        return cls(int(text[1:3], 16), int(text[3:5], 16), int(text[5:7], 16))

    @property
    def rgb(self) -> tuple[int, int, int]:
        return (self.red, self.green, self.blue)

    def __str__(self) -> str:
        return "#{:02X}{:02X}{:02X}".format(*self.rgb)


WHITE = HexColor(MAX_CHANNEL, MAX_CHANNEL, MAX_CHANNEL)


@dataclass(frozen=True)
class Params:
    max_width: int = DEFAULT_SIDE
    max_height: int = DEFAULT_SIDE
    output_format: OutputFormat = OutputFormat.PRESERVE
    jpeg_quality: int = DEFAULT_JPEG_QUALITY  # só nas saídas JPEG
    jpeg_background: HexColor = WHITE  # fundo da transparência nas saídas JPEG
    make_thumbnails: bool = False

    def __post_init__(self) -> None:
        _require_range("max_width", self.max_width, MIN_SIDE, MAX_SIDE)
        _require_range("max_height", self.max_height, MIN_SIDE, MAX_SIDE)
        _require_range("jpeg_quality", self.jpeg_quality, MIN_JPEG_QUALITY, MAX_JPEG_QUALITY)
        if self.output_format is OutputFormat.PNG:
            self._require_jpeg_options_at_default()

    def _require_jpeg_options_at_default(self) -> None:
        changed = [
            name
            for name, default in (
                ("jpeg_quality", DEFAULT_JPEG_QUALITY),
                ("jpeg_background", WHITE),
            )
            if getattr(self, name) != default
        ]
        if changed:
            raise InvalidParametersError(
                f"{', '.join(changed)} só vale para saídas JPEG; output_format é png"
            )


def params_from_mapping(raw: Mapping[str, object]) -> Params:
    """Converte a configuração decodificada de JSON; recusa campos desconhecidos e tipos errados."""
    unknown = sorted(set(raw) - _KNOWN_OPTIONS)
    if unknown:
        raise InvalidParametersError(f"opções desconhecidas: {unknown}")
    return Params(
        max_width=_integer(raw.get("max_width", DEFAULT_SIDE), "max_width"),
        max_height=_integer(raw.get("max_height", DEFAULT_SIDE), "max_height"),
        output_format=_output_format(raw.get("output_format", OutputFormat.PRESERVE.value)),
        jpeg_quality=_integer(raw.get("jpeg_quality", DEFAULT_JPEG_QUALITY), "jpeg_quality"),
        jpeg_background=HexColor.parse(
            _string(raw.get("jpeg_background", str(WHITE)), "jpeg_background")
        ),
        make_thumbnails=_boolean(raw.get("make_thumbnails", False), "make_thumbnails"),
    )


_KNOWN_OPTIONS = frozenset(field.name for field in fields(Params))


def _require_range(option: str, value: int, minimum: int, maximum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidParametersError(f"{option} deve ser inteiro")
    if not minimum <= value <= maximum:
        raise InvalidParametersError(f"{option} deve estar entre {minimum} e {maximum}")


def _output_format(raw: object) -> OutputFormat:
    try:
        return OutputFormat(_string(raw, "output_format"))
    except ValueError:
        allowed = [member.value for member in OutputFormat]
        raise InvalidParametersError(f"output_format deve ser um de {allowed}") from None


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
