"""Vídeos sintéticos gerados no teste com o FFmpeg do ambiente (nada binário no repositório).

O FFmpeg do ambiente de teste (Homebrew no Mac, `apt` na CI) é o build completo, com os encoders
que a imagem do executor não tem (libx264, libvpx, libopus); a receita, nos testes, usa esse
mesmo binário para ler. A imagem do executor roda o build mínimo sobre os mesmos arquivos em
`tests/test_executor_image.py`.

Tudo pequeno (320 x 240, 1 a 2 s), para a suíte continuar rápida; os casos de limite que exigem
tamanho (4K, 301 s) usam o mínimo de quadros que ainda prova o limite.
"""

import shutil
import subprocess
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
    "mp4_mono_and_stereo": Clip(
        inputs=(video_source(), tone(rate=22050, layout="mono"), tone(rate=48000)),
        output=(*H264, "-map", "0:v", "-map", "1:a", "-map", "2:a", *AAC, "-shortest", *MP4),
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
    _, ffprobe = require_ffmpeg()
    completed = subprocess.run(  # noqa: S603 - argv do próprio teste
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "stream=codec_name,sample_rate,channels,sample_fmt,width,height",
            "-of",
            "default=noprint_wrappers=1",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    pairs = (line.split("=", 1) for line in completed.stdout.splitlines() if "=" in line)
    return dict(pairs)


HLS_PLAYLIST = b"#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:1\n#EXTINF:1.0,\n/etc/passwd\n"
FFCONCAT = b"ffconcat version 1.0\nfile '/etc/passwd'\n"
