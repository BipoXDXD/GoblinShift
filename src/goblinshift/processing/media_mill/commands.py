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

O áudio sai cortado pelo `atrim=end_sample=N` (a duração aceita mais uma margem, em amostras,
decidida pela receita): um contêiner que declara 2 s com horas de áudio não gera horas de WAV. O
corte conta amostras decodificadas, não instantes: o `pts` vem do arquivo e pode ser comprimido
(30 s de Opus com instantes de 0 a 2 s), as amostras não. O filtro `ashowinfo`, depois do corte,
registra cada quadro de áudio (instante, amostras e taxa), para a receita saber se a faixa passava
da duração aceita.

`-loglevel level+…` prefixa cada linha do stderr com o nível (`[error]`, `[info]`), e
`read_log` separa o que é erro de decodificação e os instantes informados pelos filtros
`showinfo` (vídeo) e `ashowinfo` (áudio). O `showinfo` também informa o tamanho de cada quadro
decodificado (`s:LxA`): o cabeçalho que o ffprobe lê pode mentir, os quadros não.
"""

import re
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from goblinshift.processing.media_mill.params import AudioFormat
from goblinshift.processing.media_mill.signature import Container

JPEG_QUALITY = 3  # escala -q:v do mjpeg: 2 (melhor) a 31; 3 fica perto de qualidade 90
_ERROR_LEVELS = (b"[error]", b"[fatal]", b"[panic]")
_PTS_TIME = re.compile(rb"\bpts_time:(-?[0-9]+(?:\.[0-9]+)?)")
# `ashowinfo`: `… rate:48000 nb_samples:960 …`. O `\b` evita casar com `sample_rate:`.
_AUDIO_RATE = re.compile(rb"\brate:([1-9][0-9]*)")
_AUDIO_SAMPLES = re.compile(rb"\bnb_samples:([0-9]+)")
_FRAME_SIZE = re.compile(rb"\bs:([0-9]+)x([0-9]+)")
# Linha de um filtro: `[Parsed_showinfo_0 @ 0x…] [info] n:…`. Ancorado no início da linha, porque
# texto do arquivo (título da faixa) também aparece no log, sempre depois de `[info]` ou de espaços.
_SHOWINFO = b"[Parsed_showinfo_"
_ASHOWINFO = b"[Parsed_ashowinfo_"
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
        "-threads",
        "1",
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
    cut_samples: int,
    target: Path,
) -> list[str]:
    """Uma faixa de áudio, na taxa e nos canais de origem (sem resample nem downmix), 16 bits.

    Grava no máximo `cut_samples` amostras decodificadas; o `ashowinfo`, depois do corte, registra
    cada quadro que passou.
    """
    if isinstance(cut_samples, bool) or not isinstance(cut_samples, int) or cut_samples <= 0:
        raise ValueError(f"corte do áudio precisa ser um inteiro positivo: {cut_samples!r}")
    return [
        *_ffmpeg_head(ffmpeg, "info"),
        *source.input_options(),
        "-i",
        source.url,
        "-map",
        f"0:a:{stream_order}",
        "-vn",
        "-sn",
        "-dn",
        *_no_metadata(),
        "-af",
        f"atrim=end_sample={cut_samples},ashowinfo",
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
    # Início (s) do último quadro de áudio que passou pelo `ashowinfo`, pelo `pts`.
    last_audio_s: float | None
    # O mesmo início contado pelas amostras decodificadas antes dele (soma de `nb_samples / rate`
    # dos quadros anteriores): não depende do `pts`, que o arquivo pode comprimir.
    last_audio_by_samples_s: float | None
    # Amostras que passaram pelo `ashowinfo`, em qualquer taxa: chegar ao corte é passar dele.
    audio_samples: int
    # Quadros que passaram pelo `showinfo` e o maior lado maior e lado menor entre eles (0 sem
    # quadros), como foram decodificados, antes da escala da miniatura.
    frame_count: int
    max_long_side_px: int
    max_short_side_px: int
    # Do menor ao maior instante entre esses quadros (0 sem quadros): não fica negativo se o
    # `pts` de um arquivo hostil andar para trás.
    frame_span_s: float


def read_log(path: Path) -> LogSummary:
    """Lê o stderr linha a linha, sem carregá-lo inteiro (um arquivo hostil pode gerar muito)."""
    has_errors = False
    first: float | None = None
    last: float | None = None
    last_audio: float | None = None
    frame_count = long_side = short_side = 0
    lowest_s = highest_s = 0.0
    audio = _AudioCount()
    for line in _lines(path):
        has_errors = has_errors or any(level in line for level in _ERROR_LEVELS)
        frame_s = _frame_time(line, _SHOWINFO)
        if frame_s is not None:
            lowest_s = frame_s if first is None else min(lowest_s, frame_s)
            highest_s = frame_s if first is None else max(highest_s, frame_s)
            first = frame_s if first is None else first
            last = frame_s
            frame_count += 1
            width, height = _frame_size(line)
            long_side = max(long_side, width, height)
            short_side = max(short_side, min(width, height))
        audio_s = _frame_time(line, _ASHOWINFO)
        if audio_s is not None:
            last_audio = audio_s
        if line.startswith(_ASHOWINFO):
            audio.add(line)
    return LogSummary(
        has_errors=has_errors,
        first_frame_s=first,
        last_frame_s=last,
        last_audio_s=last_audio,
        last_audio_by_samples_s=audio.last_start_s(),
        audio_samples=audio.samples,
        frame_count=frame_count,
        max_long_side_px=long_side,
        max_short_side_px=short_side,
        frame_span_s=highest_s - lowest_s,
    )


class _AudioCount:
    """Amostras do `ashowinfo` por taxa: a soma em segundos sai exata, sem erro de float."""

    def __init__(self) -> None:
        self.samples = 0
        self._by_rate: Counter[int] = Counter()
        self._last: tuple[int, int] | None = None  # (amostras, taxa) do último quadro

    def add(self, line: bytes) -> None:
        samples = _AUDIO_SAMPLES.search(line)
        rate = _AUDIO_RATE.search(line)
        if samples is None or rate is None:
            return
        frame = (int(samples.group(1)), int(rate.group(1)))
        self.samples += frame[0]
        self._by_rate[frame[1]] += frame[0]
        self._last = frame

    def last_start_s(self) -> float | None:
        if self._last is None:
            return None
        total = sum(Fraction(samples, rate) for rate, samples in self._by_rate.items())
        return float(total - Fraction(*self._last))


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


def _frame_size(line: bytes) -> tuple[int, int]:
    match = _FRAME_SIZE.search(line)
    return (int(match.group(1)), int(match.group(2))) if match else (0, 0)


def _frame_time(line: bytes, filter_prefix: bytes) -> float | None:
    if not line.startswith(filter_prefix):
        return None
    match = _PTS_TIME.search(line)
    return float(match.group(1)) if match else None
