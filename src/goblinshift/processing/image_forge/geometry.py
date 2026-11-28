"""Dimensões dos modos de ajuste de 1.2 e da v2, sem PIL e sem ponto flutuante.

`ImageOps.contain` e `thumbnail` não servem (2.3): o primeiro amplia, e os dois arredondam. Aqui
`escala = min(max_w / w, max_h / h, 1)` é uma `Fraction`, cada dimensão é truncada e nunca fica
abaixo de 1 px; assim 2400 x 1200 numa caixa de 1600 dá exatamente 1600 x 800.

A v2 acrescenta `cover`, `pad` e escala em %. Nenhum modo amplia (D5): o `cover` de uma imagem
menor que a caixa só corta na proporção dela, e o `pad` centraliza a imagem no tamanho original.
"""

import math
from dataclasses import dataclass
from enum import StrEnum
from fractions import Fraction


class FitMode(StrEnum):
    CONTAIN = "contain"  # cabe na caixa, mantém a proporção
    COVER = "cover"  # preenche a caixa cortando o centro
    PAD = "pad"  # caixa exata, com margem em volta do `contain`
    SCALE = "scale"  # escala em porcentagem; a caixa não conta


@dataclass(frozen=True)
class Layout:
    """O que fazer com uma imagem orientada de `largura x altura`."""

    crop: tuple[int, int, int, int]  # x, y, largura e altura do recorte na imagem orientada
    content: tuple[int, int]  # tamanho do recorte depois de redimensionar
    canvas: tuple[int, int]  # tamanho final
    offset: tuple[int, int]  # posição do conteúdo no canvas


def fit_size(width: int, height: int, max_width: int, max_height: int) -> tuple[int, int]:
    """Maior tamanho com a proporção original que cabe na caixa, sem ampliar."""
    if min(width, height, max_width, max_height) < 1:
        raise ValueError(
            f"dimensões precisam ser positivas: {width}x{height} na caixa {max_width}x{max_height}"
        )
    scale = min(Fraction(max_width, width), Fraction(max_height, height), Fraction(1))
    return _scaled(width, scale), _scaled(height, scale)


def layout(
    width: int, height: int, *, fit: FitMode, box: tuple[int, int], scale_percent: int
) -> Layout:
    """Recorte, conteúdo, canvas e posição de cada modo; nenhum amplia a imagem."""
    whole = (0, 0, width, height)
    match fit:
        case FitMode.CONTAIN:
            content = fit_size(width, height, *box)
            return Layout(crop=whole, content=content, canvas=content, offset=(0, 0))
        case FitMode.COVER:
            return _cover(width, height, box)
        case FitMode.PAD:
            content = fit_size(width, height, *box)
            offset = ((box[0] - content[0]) // 2, (box[1] - content[1]) // 2)
            return Layout(crop=whole, content=content, canvas=box, offset=offset)
        case FitMode.SCALE:
            if not 1 <= scale_percent <= 100:
                raise ValueError(f"escala fora de 1 a 100: {scale_percent}")
            scale = Fraction(scale_percent, 100)
            content = (_scaled(width, scale), _scaled(height, scale))
            return Layout(crop=whole, content=content, canvas=content, offset=(0, 0))


def _cover(width: int, height: int, box: tuple[int, int]) -> Layout:
    """Maior recorte central com a proporção da caixa; vira a caixa só se a couber inteira."""
    box_width, box_height = box
    if Fraction(width, height) > Fraction(box_width, box_height):
        crop_width = min(width, _scaled(height, Fraction(box_width, box_height)))
        crop_height = height
    else:
        crop_width = width
        crop_height = min(height, _scaled(width, Fraction(box_height, box_width)))
    x, y = (width - crop_width) // 2, (height - crop_height) // 2
    fills = crop_width >= box_width and crop_height >= box_height
    content = box if fills else (crop_width, crop_height)
    return Layout(
        crop=(x, y, crop_width, crop_height), content=content, canvas=content, offset=(0, 0)
    )


def _scaled(side: int, scale: Fraction) -> int:
    return max(1, math.floor(side * scale))
