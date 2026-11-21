"""Vídeos sintéticos gerados no teste com o FFmpeg do ambiente (nada binário no repositório).

O FFmpeg do ambiente de teste (Homebrew no Mac, `apt` na CI) é o build completo, com os encoders
que a imagem do executor não tem (libx264, libvpx, libopus); a receita, nos testes, usa esse
mesmo binário para ler. A imagem do executor roda o build mínimo sobre os mesmos arquivos em
`tests/test_executor_image.py`.

Tudo pequeno (320 x 240, 1 a 2 s), para a suíte continuar rápida; os casos de limite que exigem
tamanho (4K, 301 s) usam o mínimo de quadros que ainda prova o limite.
"""

import shutil
import struct
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")

CANARY = "CANARY-6b1f0c2e-media-mill"


class MediaToolMissingError(RuntimeError):
    pass


def require_ffmpeg() -> tuple[str, str]:
    if FFMPEG is None or FFPROBE is None:
        raise MediaToolMissingError(
            "os testes do Media Mill precisam de ffmpeg e ffprobe no PATH (brew/apt install ffmpeg)"
        )
    return FFMPEG, FFPROBE


@dataclass(frozen=True)
class Clip:
    """Receita de um vídeo sintético: entradas lavfi e opções de saída do FFmpeg."""

    inputs: tuple[str, ...]
    output: tuple[str, ...]
    pre_input: tuple[str, ...] = ()


def video_source(size: str = "320x240", rate: int = 30, duration: float = 2.0) -> str:
    return f"testsrc2=size={size}:rate={rate}:duration={duration}"


def tone(rate: int = 44100, duration: float = 2.0, layout: str = "stereo") -> str:
    return (
        f"sine=frequency=440:sample_rate={rate}:duration={duration}"
        f",aformat=channel_layouts={layout}"
    )


# Uma thread: o mesmo bitstream a cada geração, para os testes não dependerem da sorte.
H264 = (
    "-c:v",
    "libx264",
    "-preset",
    "ultrafast",
    "-pix_fmt",
    "yuv420p",
    "-g",
    "15",
    "-threads",
    "1",
)
AAC = ("-c:a", "aac")
MP4 = ("-f", "mp4")
MOV = ("-f", "mov")
WEBM = ("-f", "webm")
MPEGTS = ("-f", "mpegts")


CLIPS: dict[str, Clip] = {
    "mp4": Clip(inputs=(video_source(), tone()), output=(*H264, *AAC, "-shortest", *MP4)),
    "mp4_metadata": Clip(
        inputs=(video_source(), tone()),
        output=(
            *H264,
            *AAC,
            "-metadata",
            f"title={CANARY}",
            "-metadata",
            f"location=+{CANARY}/",
            "-shortest",
            *MP4,
        ),
    ),
    "mp4_full_hd": Clip(
        inputs=(video_source(size="1920x1080", duration=0.5), tone(duration=0.5)),
        output=(*H264, *AAC, "-shortest", *MP4),
    ),
    # Faixas com durações diferentes (1 s e 2 s): taxa e canais da saída vêm do probe da faixa
    # escolhida, então só a duração prova qual faixa o `-map 0:a:N` extraiu. Sem `-shortest`, que
    # cortaria tudo em 1 s.
    "mp4_mono_and_stereo": Clip(
        inputs=(video_source(), tone(rate=22050, duration=1.0, layout="mono"), tone(rate=48000)),
        output=(*H264, "-map", "0:v", "-map", "1:a", "-map", "2:a", *AAC, *MP4),
    ),
    "mp4_no_audio": Clip(inputs=(video_source(),), output=(*H264, *MP4)),
    # `moov` no início: o arquivo cortado ainda abre, e o dano só aparece ao decodificar.
    "mp4_faststart": Clip(
        inputs=(video_source(), tone()),
        output=(*H264, *AAC, "-shortest", "-movflags", "+faststart", *MP4),
    ),
    "mp4_faststart_no_audio": Clip(
        inputs=(video_source(),), output=(*H264, "-movflags", "+faststart", *MP4)
    ),
    "mov_pcm24": Clip(
        inputs=(video_source(), tone(rate=48000)),
        output=(*H264, "-c:a", "pcm_s24le", "-shortest", *MOV),
    ),
    "mov_pcm16_mono": Clip(
        inputs=(video_source(), tone(rate=32000, layout="mono")),
        output=(*H264, "-c:a", "pcm_s16le", "-shortest", *MOV),
    ),
    "webm_vp9_opus": Clip(
        inputs=(video_source(), tone(rate=48000)),
        output=(
            "-c:v",
            "libvpx-vp9",
            "-deadline",
            "realtime",
            "-c:a",
            "libopus",
            "-shortest",
            *WEBM,
        ),
    ),
    "webm_vp8_vorbis": Clip(
        inputs=(video_source(), tone(rate=44100)),
        output=(
            "-c:v",
            "libvpx",
            "-deadline",
            "realtime",
            "-c:a",
            "vorbis",
            "-strict",
            "-2",
            "-ac",
            "2",
            "-shortest",
            *WEBM,
        ),
    ),
    # Vídeo de 1 s com áudio de 3 s, num WebM (o Matroska não declara a duração de cada faixa):
    # a miniatura de 90% (2,7 s) não tem quadro em ou depois do alvo.
    "webm_short_video": Clip(
        inputs=(video_source(duration=1.0), tone(rate=48000, duration=3.0)),
        output=("-c:v", "libvpx-vp9", "-deadline", "realtime", "-c:a", "libopus", *WEBM),
    ),
    # Base do WebM que mente a duração (`DERIVED`): vídeo de 2 s e Opus de 30 s, sem `-shortest`.
    "webm_2s_audio_30s": Clip(
        inputs=(video_source(duration=2.0), tone(rate=48000, duration=30.0)),
        output=("-c:v", "libvpx-vp9", "-deadline", "realtime", "-c:a", "libopus", *WEBM),
    ),
    # Base do WebM com instantes de áudio comprimidos (`DERIVED`): vídeo de 2 s e Opus de 2,8 s,
    # menos que a duração mais a margem do corte (3 s), para só a contagem de amostras recusar.
    "webm_2s_audio_2800ms": Clip(
        inputs=(video_source(duration=2.0), tone(rate=48000, duration=2.8)),
        output=("-c:v", "libvpx-vp9", "-deadline", "realtime", "-c:a", "libopus", *WEBM),
    ),
    # Trechos do MP4 que troca de resolução (`DERIVED`): o MPEG-TS se emenda byte a byte.
    "ts_320x240": Clip(
        inputs=(video_source(duration=1.0), tone(duration=1.0)),
        output=(*H264, *AAC, "-shortest", *MPEGTS),
    ),
    "ts_2560x1440": Clip(
        inputs=(video_source(size="2560x1440", duration=1.0), tone(duration=1.0)),
        output=(*H264, *AAC, "-shortest", *MPEGTS),
    ),
    # Trechos do WebM que troca de resolução depois do último alvo (`DERIVED`): 1 s de vídeo em
    # 16 s de Opus, então as três miniaturas caem depois do último quadro e leem o vídeo inteiro.
    "webm_320x240_1s": Clip(
        inputs=(video_source(duration=1.0), tone(rate=48000, duration=1.0)),
        output=("-c:v", "libvpx-vp9", "-deadline", "realtime", "-c:a", "libopus", *WEBM),
    ),
    "webm_2560x1440_audio_15s": Clip(
        inputs=(video_source(size="2560x1440", duration=0.1), tone(rate=48000, duration=15.0)),
        output=("-c:v", "libvpx-vp9", "-deadline", "realtime", "-c:a", "libopus", *WEBM),
    ),
    # Base do WebM que declara ~60 fps com 240 (`DERIVED`): vídeo de 1 s e Opus de 3 s, para a
    # miniatura de 50% cair depois do último quadro e ler o vídeo inteiro.
    "webm_240fps_1s_audio_3s": Clip(
        inputs=(video_source(rate=240, duration=1.0), tone(rate=48000, duration=3.0)),
        output=("-c:v", "libvpx-vp9", "-deadline", "realtime", "-c:a", "libopus", *WEBM),
    ),
    # Hostis e fora da allowlist.
    "mp4_mpeg4": Clip(
        inputs=(video_source(), tone()), output=("-c:v", "mpeg4", *AAC, "-shortest", *MP4)
    ),
    "mp4_4k": Clip(
        inputs=(video_source(size="3840x2160", rate=1, duration=1.0),),
        output=(*H264, *MP4),
    ),
    "mp4_120fps": Clip(inputs=(video_source(rate=120, duration=0.5),), output=(*H264, *MP4)),
    "mp4_six_channels": Clip(
        inputs=(video_source(), tone(rate=48000, layout="5.1")),
        output=(*H264, *AAC, "-shortest", *MP4),
    ),
    "mp4_96khz": Clip(
        inputs=(video_source(), tone(rate=96000)), output=(*H264, *AAC, "-shortest", *MP4)
    ),
    "mp4_301s": Clip(
        inputs=(video_source(size="64x64", rate=1, duration=301),), output=(*H264, *MP4)
    ),
    "mp4_anamorphic": Clip(inputs=(video_source(),), output=(*H264, "-vf", "setsar=4/3", *MP4)),
}


def generate(clip: Clip, target: Path) -> Path:
    ffmpeg, _ = require_ffmpeg()
    argv = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *clip.pre_input]
    for source in clip.inputs:
        argv += ["-f", "lavfi", "-i", source]
    argv += [*clip.output, str(target)]
    subprocess.run(argv, check=True, capture_output=True)  # noqa: S603 - argv do próprio teste
    return target


def rotated(source: Path, target: Path, degrees: int) -> Path:
    """Mesmo conteúdo, com a matriz de exibição girada (como um vídeo de celular em pé)."""
    ffmpeg, _ = require_ffmpeg()
    argv = [
        ffmpeg,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-display_rotation:v:0",
        str(degrees),
        "-i",
        str(source),
        "-c",
        "copy",
        str(target),
    ]
    subprocess.run(argv, check=True, capture_output=True)  # noqa: S603 - argv do próprio teste
    return target


# ID do elemento `Duration` do Segment Info do Matroska seguido do tamanho (8 bytes, um float64).
_MATROSKA_DURATION = b"\x44\x89\x88"


def with_declared_duration(source: Path, target: Path, duration_ms: float) -> Path:
    """Mesmo WebM, com o `Duration` do Segment Info trocado: o ffprobe passa a relatar o falso."""
    data = source.read_bytes()
    assert data.count(_MATROSKA_DURATION) == 1
    start = data.index(_MATROSKA_DURATION) + len(_MATROSKA_DURATION)
    target.write_bytes(data[:start] + struct.pack(">d", duration_ms) + data[start + 8 :])
    return target


# ID do `DefaultDuration` da faixa (ns por quadro) seguido do tamanho: 3 bytes no WebM de 240 fps.
_MATROSKA_DEFAULT_DURATION = b"\x23\xe3\x83\x83"


def with_declared_frame_duration(source: Path, target: Path) -> Path:
    """Mesmo WebM, com o `DefaultDuration` do vídeo no maior valor de 3 bytes (16.777.215 ns):
    o ffprobe passa a declarar ~59,6 fps, dentro do teto, e os quadros continuam lá."""
    data = source.read_bytes()
    assert data.count(_MATROSKA_DEFAULT_DURATION) == 1
    start = data.index(_MATROSKA_DEFAULT_DURATION) + len(_MATROSKA_DEFAULT_DURATION)
    target.write_bytes(data[:start] + b"\xff\xff\xff" + data[start + 3 :])
    return target


def with_audio_timestamps_divided(source: Path, target: Path, divisor: int) -> Path:
    """Mesmo WebM, com o `pts` de cada pacote de áudio dividido por `divisor` (filtro `setts`, sem
    reencodar): as amostras continuam lá, e os instantes passam a caber na duração do vídeo."""
    ffmpeg, _ = require_ffmpeg()
    argv = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source)]
    argv += ["-map", "0", "-c", "copy", "-bsf:a", f"setts=ts=PTS/{divisor}", *WEBM, str(target)]
    subprocess.run(argv, check=True, capture_output=True)  # noqa: S603 - argv do próprio teste
    return target


def concatenated(parts: Sequence[Path], target: Path) -> Path:
    """MPEG-TS emendados pelo protocolo `concat` e copiados para MP4, sem reencodar: o `moov`
    descreve só o primeiro trecho (largura e altura), e os quadros dos outros seguem como vieram."""
    ffmpeg, _ = require_ffmpeg()
    joined = "concat:" + "|".join(str(part) for part in parts)
    argv = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", joined]
    argv += ["-c", "copy", *MP4, str(target)]
    subprocess.run(argv, check=True, capture_output=True)  # noqa: S603 - argv do próprio teste
    return target


def joined(parts: Sequence[Path], target: Path) -> Path:
    """WebMs emendados pelo demuxer `concat` e copiados, sem reencodar: a faixa declara a largura
    e a altura do primeiro, e o VP9 dos outros troca de tamanho no keyframe."""
    ffmpeg, _ = require_ffmpeg()
    listing = target.with_suffix(".txt")
    listing.write_text("".join(f"file '{part}'\n" for part in parts))
    argv = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat"]
    argv += ["-safe", "0", "-i", str(listing), "-c", "copy", *WEBM, str(target)]
    subprocess.run(argv, check=True, capture_output=True)  # noqa: S603 - argv do próprio teste
    return target


def _resolution_switch(root: Path) -> Path:
    """Declara 320x240 e passa a 2560x1440 em 1 s: a miniatura de 90% sai do trecho grande."""
    small = generate(CLIPS["ts_320x240"], root / "ts_320x240.ts")
    large = generate(CLIPS["ts_2560x1440"], root / "ts_2560x1440.ts")
    return concatenated([small, large], root / "mp4_resolution_switch.mp4")


def _resolution_switch_before_target(root: Path) -> Path:
    """Declara 320x240 e passa a 2560x1440 em 1 s; só o último quadro anterior chega lá."""
    small = generate(CLIPS["webm_320x240_1s"], root / "webm_320x240_1s.webm")
    large = generate(CLIPS["webm_2560x1440_audio_15s"], root / "webm_2560x1440_audio_15s.webm")
    return joined([small, large], root / "webm_resolution_switch_before_target.webm")


def _fps_beyond_declared(root: Path) -> Path:
    """240 quadros em 1 s, declarando ~59,6 fps; a miniatura de 50% lê todos (último anterior)."""
    base = generate(CLIPS["webm_240fps_1s_audio_3s"], root / "webm_240fps_1s_audio_3s.webm")
    return with_declared_frame_duration(base, root / "webm_fps_beyond_declared.webm")


def _audio_beyond_duration(root: Path) -> Path:
    """Declara 2 s, a duração do vídeo, com 30 s de Opus: o probe aceita e o áudio sairia 15x."""
    base = generate(CLIPS["webm_2s_audio_30s"], root / "webm_2s_audio_30s.webm")
    return with_declared_duration(base, root / "webm_audio_beyond_duration.webm", 2000.0)


def _audio_timestamps_compressed(root: Path) -> Path:
    """Vídeo de 2 s com 2,8 s de Opus em instantes de 0 a 1,4 s: o contêiner declara 2 s, o `pts`
    do último quadro fica dentro deles e o áudio sairia 0,8 s mais longo que o vídeo."""
    base = generate(CLIPS["webm_2s_audio_2800ms"], root / "webm_2s_audio_2800ms.webm")
    return with_audio_timestamps_divided(base, root / "webm_audio_timestamps_compressed.webm", 2)


# Vídeos derivados de um clipe gerado: recebem o diretório da biblioteca e devolvem o arquivo.
DERIVED: dict[str, Callable[[Path], Path]] = {
    "webm_audio_beyond_duration": _audio_beyond_duration,
    "webm_audio_timestamps_compressed": _audio_timestamps_compressed,
    "mp4_resolution_switch": _resolution_switch,
    "webm_resolution_switch_before_target": _resolution_switch_before_target,
    "webm_fps_beyond_declared": _fps_beyond_declared,
}


def truncated(data: bytes) -> bytes:
    return data[: len(data) // 2]


def corrupted(data: bytes) -> bytes:
    """Mesmo tamanho, com o trecho de 55% a 75% embaralhado: o meio do vídeo não decodifica."""
    damaged = bytearray(data)
    for index in range(len(data) * 55 // 100, len(data) * 75 // 100):
        damaged[index] ^= 0x5A
    return bytes(damaged)


def probe(path: Path) -> dict[str, str]:
    """Primeira faixa de `path` (áudio ou imagem) pelo ffprobe do ambiente, em `chave=valor`."""
    output = _show_entries(path, "stream=codec_name,sample_rate,channels,sample_fmt,width,height")
    pairs = (line.split("=", 1) for line in output.splitlines() if "=" in line)
    return dict(pairs)


def duration_s(path: Path) -> float:
    """Duração do arquivo em segundos, pelo contêiner, medida pelo ffprobe do ambiente."""
    return float(_show_entries(path, "format=duration").splitlines()[0].split("=", 1)[1])


def stream_kinds(path: Path) -> list[str]:
    """Tipo (`audio`, `video`...) de cada faixa de `path`, na ordem do arquivo."""
    lines = _show_entries(path, "stream=codec_type").splitlines()
    return [line.split("=", 1)[1] for line in lines if line.startswith("codec_type=")]


def _show_entries(path: Path, entries: str) -> str:
    return run_ffprobe(path, "-show_entries", entries, "-of", "default=noprint_wrappers=1")


def run_ffprobe(path: Path, *options: str) -> str:
    """Saída do ffprobe do ambiente para `path`, com `options` e só os erros no stderr."""
    _, ffprobe = require_ffmpeg()
    completed = subprocess.run(  # noqa: S603 - argv do próprio teste, sem shell
        [ffprobe, "-v", "error", *options, str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout


HLS_PLAYLIST = b"#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:1\n#EXTINF:1.0,\n/etc/passwd\n"
FFCONCAT = b"ffconcat version 1.0\nfile '/etc/passwd'\n"
