"""Linhas de comando do ffprobe e do FFmpeg, sempre montadas daqui (2.3; ADR 036).

Nenhum texto do usuário entra numa linha de comando: os parâmetros são enums e inteiros já
validados, e o único caminho é o da entrada (absoluto, fixado pelo executor) com o prefixo
`file:`, que impede que um `:` no caminho vire protocolo. Toda leitura usa:

- `-nostdin` (no FFmpeg) e `-hide_banner`;
- `-protocol_whitelist file`: nenhum outro protocolo, nem `pipe`, `http`, `crypto` ou `data`;
- `-f mov` ou `-f matroska`, pela assinatura detectada: o demuxer nunca é adivinhado pelo
  conteúdo (sem `hls`, `concat`, `image2` com padrão nem playlists);
- `-enable_drefs 0` no MOV: o demuxer não segue referências a outros arquivos (data references);
- `-threads 1` e `-filter_threads 1`: o executor tem 1 vCPU e a memória fica previsível.

Na saída, `-map_metadata -1` e `-map_chapters -1` descartam os metadados do vídeo (local de
gravação, aparelho) e `+bitexact` tira a versão do encoder do arquivo, para a mesma entrada dar os
mesmos bytes. `-n` recusa sobrescrever: cada saída vai para um caminho novo no rascunho.

`-loglevel level+…` prefixa cada linha do stderr com o nível (`[error]`, `[info]`), e
`read_log` separa o que é erro de decodificação e os instantes informados pelo filtro `showinfo`.
"""

import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from goblinshift.processing.media_mill.params import AudioFormat
from goblinshift.processing.media_mill.signature import Container

JPEG_QUALITY = 3  # escala -q:v do mjpeg: 2 (melhor) a 31; 3 fica perto de qualidade 90
_ERROR_LEVELS = (b"[error]", b"[fatal]", b"[panic]")
_PTS_TIME = re.compile(rb"\bpts_time:(-?[0-9]+(?:\.[0-9]+)?)")
_SHOWINFO = b"Parsed_showinfo"
_AUDIO_ENCODERS = {
    AudioFormat.WAV: ("-c:a", "pcm_s16le", "-f", "wav"),
    # O FLAC aceita 16 e 32 bits; a amostra é fixada em 16 bits (1.5).
    AudioFormat.FLAC: ("-c:a", "flac", "-sample_fmt", "s16", "-f", "flac"),
}
PROBE_ENTRIES = (
    "format=format_name,duration"
    ":stream=index,codec_type,codec_name,width,height,sample_aspect_ratio,avg_frame_rate,"
    "r_frame_rate,duration,sample_rate,channels"
    ":stream_side_data=rotation"
)


@dataclass(frozen=True)
class Source:
    """Entrada já com o contêiner detectado pela assinatura."""

    path: Path
    container: Container

    def __post_init__(self) -> None:
        if not self.path.is_absolute():
            raise ValueError(f"a entrada precisa de caminho absoluto: {self.path}")

    def input_options(self) -> list[str]:
        demuxer_options = ["-enable_drefs", "0"] if self.container is Container.MOV else []
        return ["-protocol_whitelist", "file", "-f", self.container.demuxer, *demuxer_options]

    @property
    def url(self) -> str:
        return f"file:{self.path}"


@dataclass(frozen=True)
class Box:
    width: int
    height: int


def probe_argv(ffprobe: Path, source: Source) -> list[str]:
    return [
        str(ffprobe),
        "-hide_banner",
        "-loglevel",
        "error",
        *source.input_options(),
        "-show_entries",
        PROBE_ENTRIES,
        "-of",
        "json",
        "-i",
        source.url,
    ]


def audio_argv(
    ffmpeg: Path,
    source: Source,
    *,
    stream_order: int,
    sample_rate: int,
    channels: int,
    audio_format: AudioFormat,
    target: Path,
) -> list[str]:
    """Uma faixa de áudio, na taxa e nos canais de origem (sem resample nem downmix), 16 bits."""
    return [
        *_ffmpeg_head(ffmpeg, "error"),
        *source.input_options(),
        "-i",
        source.url,
        "-map",
        f"0:a:{stream_order}",
        "-vn",
        "-sn",
        "-dn",
        *_no_metadata(),
        "-ar",
        str(sample_rate),
        "-ac",
        str(channels),
        *_AUDIO_ENCODERS[audio_format],
        *_bitexact("a"),
        "-n",
        str(target),
    ]


def thumbnail_argv(
    ffmpeg: Path, source: Source, *, at_s: float, box: Box, target: Path
) -> list[str]:
    """Primeiro quadro com instante maior ou igual a `at_s` (busca precisa a partir do keyframe)."""
    return [
        *_ffmpeg_head(ffmpeg, "info"),
        *source.input_options(),
        "-ss",
        _seconds(at_s),
        "-i",
        source.url,
        *_thumbnail_output(box),
        "-frames:v",
        "1",
        "-n",
        str(target),
    ]


def last_frame_before_argv(
    ffmpeg: Path, source: Source, *, until_s: float, box: Box, target: Path
) -> list[str]:
    """Último quadro antes de `until_s`: lê do início e reescreve a mesma imagem a cada quadro."""
    return [
        *_ffmpeg_head(ffmpeg, "info"),
        *source.input_options(),
        "-t",
        _seconds(until_s),
        "-i",
        source.url,
        *_thumbnail_output(box),
        "-n",
        str(target),
    ]


@dataclass(frozen=True)
class LogSummary:
    has_errors: bool
    # Instantes (s) dos quadros que passaram pelo `showinfo`, relativos ao início da leitura.
    first_frame_s: float | None
    last_frame_s: float | None


def read_log(path: Path) -> LogSummary:
    """Lê o stderr linha a linha, sem carregá-lo inteiro (um arquivo hostil pode gerar muito)."""
    has_errors = False
    first: float | None = None
    last: float | None = None
    for line in _lines(path):
        has_errors = has_errors or any(level in line for level in _ERROR_LEVELS)
        frame_s = _frame_time(line)
        if frame_s is not None:
            first = frame_s if first is None else first
            last = frame_s
    return LogSummary(has_errors, first, last)


def _ffmpeg_head(ffmpeg: Path, level: str) -> list[str]:
    return [
        str(ffmpeg),
        "-nostdin",
        "-hide_banner",
        "-nostats",
        "-loglevel",
        f"level+{level}",
        "-threads",
        "1",
        "-filter_threads",
        "1",
    ]


def _thumbnail_output(box: Box) -> list[str]:
    # `showinfo` antes da escala registra o instante do quadro escolhido. A rotação de 90/180/270
    # é aplicada pelo autorotate do FFmpeg antes do filtro, então `box` já vem em pé.
    video_filter = f"showinfo,scale={box.width}:{box.height}:flags=lanczos,format=yuvj420p"
    return [
        "-map",
        "0:v:0",
        "-an",
        "-sn",
        "-dn",
        *_no_metadata(),
        "-vf",
        video_filter,
        "-threads",
        "1",
        "-c:v",
        "mjpeg",
        "-q:v",
        str(JPEG_QUALITY),
        *_bitexact("v"),
        "-f",
        "image2",
        "-update",
        "1",
    ]


def _no_metadata() -> list[str]:
    return ["-map_metadata", "-1", "-map_chapters", "-1"]


def _bitexact(stream: str) -> list[str]:
    return ["-fflags", "+bitexact", f"-flags:{stream}", "+bitexact"]


def _seconds(value: float) -> str:
    return f"{value:.6f}"


def _lines(path: Path) -> Iterator[bytes]:
    with path.open("rb") as log:
        yield from log


def _frame_time(line: bytes) -> float | None:
    if _SHOWINFO not in line:
        return None
    match = _PTS_TIME.search(line)
    return float(match.group(1)) if match else None
