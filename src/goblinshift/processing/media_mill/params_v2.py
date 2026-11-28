"""Parâmetros do Media Mill v2: áudio com perda (MP3, AAC e Opus) e corte (ADR 072).

O `AudioFormat` da v1 fica intocado (1.6: dentro de uma versão publicada, enums não mudam); a v2
tem o enum próprio, superconjunto do da v1. As listas de bitrate são as de D6 (a): todos os valores
valem em 44,1/48 kHz, mono ou estéreo, sem o encoder cortar o pedido em silêncio.
"""

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType


class AudioFormatV2(StrEnum):
    WAV = "wav"  # PCM 16-bit little-endian, como na v1
    FLAC = "flac"  # FLAC 16-bit, como na v1
    MP3 = "mp3"  # LAME, sem ID3 nem cabeçalho Xing
    AAC = "aac"  # AAC nativo do FFmpeg, em .m4a
    OPUS = "opus"  # libopus, sempre a 48 kHz


DEFAULT_BITRATE_KBPS = 192
LOSSY = frozenset({AudioFormatV2.MP3, AudioFormatV2.AAC, AudioFormatV2.OPUS})
BITRATES_KBPS: Mapping[AudioFormatV2, tuple[int, ...]] = MappingProxyType(
    {
        AudioFormatV2.MP3: (128, 160, 192, 256, 320),
        AudioFormatV2.AAC: (96, 128, 160, 192, 256),
        AudioFormatV2.OPUS: (64, 96, 128, 160, 192),
    }
)
