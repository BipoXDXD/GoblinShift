"""Dimensões do ajuste `contain` de 1.2, sem PIL e sem ponto flutuante.

`ImageOps.contain` e `thumbnail` não servem (2.3): o primeiro amplia, e os dois arredondam. Aqui
`escala = min(max_w / w, max_h / h, 1)` é uma `Fraction`, cada dimensão é truncada e nunca fica
abaixo de 1 px; assim 2400 x 1200 numa caixa de 1600 dá exatamente 1600 x 800.
"""

import math
from fractions import Fraction


def fit_size(width: int, height: int, max_width: int, max_height: int) -> tuple[int, int]:
    """Maior tamanho com a proporção original que cabe na caixa, sem ampliar."""
    if min(width, height, max_width, max_height) < 1:
        raise ValueError(
            f"dimensões precisam ser positivas: {width}x{height} na caixa {max_width}x{max_height}"
        )
    scale = min(Fraction(max_width, width), Fraction(max_height, height), Fraction(1))
    return _scaled(width, scale), _scaled(height, scale)


def _scaled(side: int, scale: Fraction) -> int:
    return max(1, math.floor(side * scale))
