"""Limites do Image Forge (docs/GoblinShiftPlan.md, 1.7)."""

from dataclasses import dataclass

# 100 imagens + 100 miniaturas = 200 saídas, abaixo do teto de 220 arquivos de 1.7.
MAX_INPUTS = 100


@dataclass(frozen=True)
class Limits:
    # 24 megapixels em base decimal; conferido antes de decodificar (1.2).
    max_pixels: int = 24_000_000
    max_axis: int = 8192  # px por eixo
    thumbnail_box: int = 320  # a miniatura cabe em 320 x 320, sem ampliar
