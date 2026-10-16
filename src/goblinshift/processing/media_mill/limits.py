"""Limites do Media Mill (docs/GoblinShiftPlan.md, 1.5 e 1.7; ADR 036)."""

from dataclasses import dataclass

MIB = 1024 * 1024
# 5 vídeos x (1 áudio + 3 miniaturas) = 20 saídas, longe do teto de 220 arquivos de 1.7.
MAX_INPUTS = 5
# 1.7: "vídeo, quando disponível, até 256 MiB"; o lote segue o teto comum de 512 MiB.
MAX_INPUT_BYTES = 256 * MIB
MAX_TOTAL_INPUT_BYTES = 512 * MIB


@dataclass(frozen=True)
class Limits:
    max_duration_s: int = 300
    # Lado maior e lado menor, para 1080 x 1920 (retrato gravado sem rotação) também caber.
    max_long_side_px: int = 1920
    max_short_side_px: int = 1080
    max_fps: int = 60
    max_sample_rate_hz: int = 48_000
    max_channels: int = 2
    thumbnail_max_side_px: int = 640
