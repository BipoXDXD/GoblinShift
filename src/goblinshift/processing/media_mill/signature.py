"""Contêiner pela assinatura dos primeiros bytes (docs/GoblinShiftPlan.md, 2.3; ADR 036).

A extensão e o Content-Type do envio são indícios, nunca validação (3.6). O demuxer de cada
chamada ao FFmpeg sai daqui, forçado com `-f`: assim nenhum conteúdo disfarçado (playlist HLS
renomeada para `.mp4`, lista do `concat`, `.ffconcat`) escolhe um demuxer que abre outros arquivos.
"""

from enum import Enum

from goblinshift.processing.media_mill.rejection import MediaRejected, RejectionCode

# O suficiente para a caixa inicial do ISO BMFF e o cabeçalho EBML com o DocType.
HEADER_BYTES = 64
_ISO_BOX_TYPE = slice(4, 8)
# Caixas que abrem um MP4 (`ftyp`) ou um QuickTime antigo, sem `ftyp`.
_ISO_LEADING_BOXES = frozenset({b"ftyp", b"moov", b"mdat", b"wide", b"free", b"skip"})
_EBML_MAGIC = b"\x1a\x45\xdf\xa3"
# Elemento DocType (0x4282), tamanho 4 em VINT de um byte (0x84) e o valor "webm".
_WEBM_DOC_TYPE = b"\x42\x82\x84webm"


class Container(Enum):
    MOV = "mov"  # MP4 e MOV, H.264 com AAC ou PCM
    WEBM = "matroska"  # WebM, VP8/VP9 com Opus/Vorbis

    @property
    def demuxer(self) -> str:
        return self.value


def detect_container(header: bytes) -> Container:
    """Família do contêiner pelos primeiros `HEADER_BYTES` bytes, ou `MediaRejected`."""
    if len(header) >= _ISO_BOX_TYPE.stop and header[_ISO_BOX_TYPE] in _ISO_LEADING_BOXES:
        return Container.MOV
    if header.startswith(_EBML_MAGIC) and _WEBM_DOC_TYPE in header[:HEADER_BYTES]:
        return Container.WEBM
    raise MediaRejected(
        RejectionCode.UNSUPPORTED_FORMAT, "o arquivo não é MP4, MOV nem WebM pela assinatura"
    )
