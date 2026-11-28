"""Recompressão Flate sem perdas, com tetos de bytes decodificados (ADR 070).

O qpdf nunca decodifica nada na gravação (`stream_decode_level=none`). O `recompress_flate` dele
exigiria `stream_decode_level` generalizado, e então o qpdf decodificaria também LZW e RunLength,
sem teto nosso. Por isso a recompressão é feita aqui, só para streams Flate, em blocos: o texto
decodificado nunca fica inteiro na memória, e dois tetos de bytes decodificados (por stream e por
execução) fazem uma bomba de descompressão ficar como está, sem estourar memória nem tempo.

Imagens não são reamostradas: os bytes decodificados de cada stream continuam idênticos, e o
`/DecodeParms` (o preditor PNG, por exemplo) fica como estava.
"""

import zlib
from dataclasses import dataclass

import pikepdf

from goblinshift.processing.pdf_press.limits import (
    MAX_RUN_DECODED_BYTES,
    MAX_STREAM_DECODED_BYTES,
    MIB,
)

__all__ = [
    "DECODE_CHUNK_BYTES",
    "MAX_RUN_DECODED_BYTES",
    "MAX_STREAM_DECODED_BYTES",
    "ZLIB_LEVEL",
    "FlateRecompressor",
    "RecompressionStats",
]

ZLIB_LEVEL = 9
DECODE_CHUNK_BYTES = 1 * MIB


@dataclass(frozen=True)
class RecompressionStats:
    recompressed: int  # streams Flate regravados menores
    kept: int  # streams com filtro que ficaram como estavam

    def __add__(self, other: RecompressionStats) -> RecompressionStats:
        return RecompressionStats(self.recompressed + other.recompressed, self.kept + other.kept)


class FlateRecompressor:
    """Uma instância por execução: guarda o orçamento de bytes decodificados que resta."""

    def __init__(self, max_stream_decoded_bytes: int, max_run_decoded_bytes: int) -> None:
        self._max_stream_decoded_bytes = max_stream_decoded_bytes
        self._budget = max_run_decoded_bytes

    def recompress(self, pdf: pikepdf.Pdf) -> RecompressionStats:
        """Regrava menor cada stream só com `/FlateDecode`; os demais com filtro ficam mantidos.

        Streams sem filtro ficam para o `compress_streams` do qpdf, como na v1.
        """
        recompressed = kept = 0
        for candidate in pdf.objects:
            if not isinstance(candidate, pikepdf.Stream) or "/Filter" not in candidate:
                continue
            if _is_flate_only(candidate.Filter) and self._recompress_stream(candidate):
                recompressed += 1
            else:
                kept += 1
        return RecompressionStats(recompressed, kept)

    def _recompress_stream(self, stream: pikepdf.Stream) -> bool:
        """Regrava `stream` se couber nos tetos e ficar menor; senão, deixa como está."""
        limit = min(self._max_stream_decoded_bytes, self._budget)
        if limit <= 0:
            return False
        raw = stream.read_raw_bytes()
        decoded, encoded = _reencoded(raw, limit)
        self._budget = max(0, self._budget - decoded)
        if encoded is None or len(encoded) >= len(raw):
            return False
        # O preditor (PNG, TIFF) descreve os bytes decodificados, que não mudam: fica.
        parms = stream.get("/DecodeParms")
        kept_parms = parms if isinstance(parms, pikepdf.Dictionary | pikepdf.Array) else None
        stream.write(encoded, filter=pikepdf.Name.FlateDecode, decode_parms=kept_parms)
        return True


def _is_flate_only(filters: pikepdf.Object) -> bool:
    if isinstance(filters, pikepdf.Array):
        return len(filters) == 1 and filters[0] == pikepdf.Name.FlateDecode
    return bool(filters == pikepdf.Name.FlateDecode)


def _reencoded(raw: bytes, limit: int) -> tuple[int, bytes | None]:
    """Decodifica `raw` em blocos e recomprime cada um; devolve os bytes decodificados e o novo.

    O novo é None quando passa de `limit`, quando o zlib recusa os dados ou quando o fluxo
    termina sem o fim do zlib (stream truncado).
    """
    decoder = zlib.decompressobj()
    encoder = zlib.compressobj(ZLIB_LEVEL)
    parts: list[bytes] = []
    decoded = 0
    pending = raw
    try:
        while not decoder.eof:
            chunk = decoder.decompress(pending, DECODE_CHUNK_BYTES)
            decoded += len(chunk)
            if decoded > limit:
                return decoded, None
            parts.append(encoder.compress(chunk))
            pending = decoder.unconsumed_tail
            if not pending and len(chunk) < DECODE_CHUNK_BYTES:
                break
    except zlib.error:
        return decoded, None
    if not decoder.eof:
        return decoded, None
    parts.append(encoder.flush())
    return decoded, b"".join(parts)
