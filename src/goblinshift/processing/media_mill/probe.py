"""Saída do ffprobe → fatos do vídeo → allowlist e limites de 1.5 (ADR 036).

Duas etapas puras, sem subprocesso: `parse_probe` converte o JSON (que descreve conteúdo do
usuário e por isso é dado não confiável) em tipos próprios, e `inspect` aplica a allowlist de
contêiner e codec, os limites e a escolha da faixa de áudio. Qualquer coisa fora da forma
esperada vira `UNSUPPORTED_FORMAT`, nunca exceção crua.

Só a primeira faixa de vídeo é decodificada (miniaturas) e só a faixa de áudio escolhida é
extraída; ainda assim, toda faixa de vídeo e de áudio precisa estar na allowlist, para um codec
estranho não ficar escondido numa faixa secundária. Faixas de outros tipos (dados, legendas,
timecode do QuickTime) nunca são mapeadas e por isso são ignoradas.
"""

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction

from goblinshift.processing.media_mill.limits import Limits
from goblinshift.processing.media_mill.params import Params
from goblinshift.processing.media_mill.rejection import MediaRejected, RejectionCode
from goblinshift.processing.media_mill.signature import Container

MAX_STREAMS = 16
RIGHT_ANGLE = 90
FULL_TURN = 360
# Nome que o ffprobe dá a cada demuxer forçado; outro nome indica demuxer inesperado.
_FORMAT_NAMES = {
    Container.MOV: "mov,mp4,m4a,3gp,3g2,mj2",
    Container.WEBM: "matroska,webm",
}
_VIDEO_CODECS = {
    Container.MOV: frozenset({"h264"}),
    Container.WEBM: frozenset({"vp8", "vp9"}),
}
_AUDIO_CODECS = {
    Container.MOV: frozenset({"aac", "pcm_s16le", "pcm_s16be", "pcm_s24le", "pcm_s24be"}),
    Container.WEBM: frozenset({"opus", "vorbis"}),
}
# Pixel quadrado: "1:1", ou "0:1"/ausente quando o arquivo não declara (o FFmpeg assume 1:1).
_SQUARE_PIXEL_RATIOS = frozenset({"1:1", "0:1", None})


@dataclass(frozen=True)
class VideoStream:
    codec: str
    width: int
    height: int
    sample_aspect_ratio: str | None
    frame_rate: Fraction | None  # None quando o arquivo não declara
    rotation: int  # graus, como o ffprobe informa (pode ser negativo)
    duration_s: float | None


@dataclass(frozen=True)
class AudioStream:
    order: int  # posição entre as faixas de áudio: o N de `0:a:N`
    codec: str
    sample_rate: int
    channels: int


@dataclass(frozen=True)
class MediaInfo:
    format_name: str
    duration_s: float | None
    videos: tuple[VideoStream, ...]
    audios: tuple[AudioStream, ...]


@dataclass(frozen=True)
class Inspected:
    """Vídeo aceito: o que as conversões precisam saber."""

    container: Container
    codec: str
    width: int  # dimensões codificadas
    height: int
    rotation: int  # 0, 90, 180 ou 270
    frame_rate: Fraction
    duration_s: float  # do contêiner, para o limite
    video_duration_s: float  # da faixa de vídeo, base dos instantes das miniaturas
    audio: AudioStream | None  # a faixa escolhida; None quando não se extrai áudio

    @property
    def display_width(self) -> int:
        return self.height if self.rotation % (2 * RIGHT_ANGLE) else self.width

    @property
    def display_height(self) -> int:
        return self.width if self.rotation % (2 * RIGHT_ANGLE) else self.height


def parse_probe(data: bytes) -> MediaInfo:
    """JSON de `ffprobe -show_entries … -of json` → `MediaInfo`, ou `MediaRejected`."""
    try:
        document = json.loads(data)
        return _media_info(_mapping(document))
    except (ValueError, TypeError, KeyError) as error:
        raise _unsupported("o ffprobe não descreveu o arquivo como vídeo legível") from error


def inspect(info: MediaInfo, container: Container, params: Params, limits: Limits) -> Inspected:
    """Aplica allowlist, perfil e limites; devolve o vídeo aceito ou levanta `MediaRejected`."""
    _require_allowlisted(info, container)
    video = info.videos[0]
    duration = _checked_duration(info, video, limits)
    _require_size_within(video, limits)
    frame_rate = _checked_frame_rate(video, limits)
    _require_square_pixels(video)
    return Inspected(
        container=container,
        codec=video.codec,
        width=video.width,
        height=video.height,
        rotation=_checked_rotation(video),
        frame_rate=frame_rate,
        duration_s=duration,
        video_duration_s=video.duration_s or duration,
        audio=_chosen_audio(info.audios, params, limits) if params.extract_audio else None,
    )


def _media_info(document: Mapping[str, object]) -> MediaInfo:
    streams = document["streams"]
    if not isinstance(streams, list):
        raise TypeError("streams não é lista")
    if len(streams) > MAX_STREAMS:
        raise _unsupported(f"o arquivo tem mais de {MAX_STREAMS} faixas")
    entries = [_mapping(stream) for stream in streams]
    file_format = _mapping(document["format"])
    return MediaInfo(
        format_name=_text(file_format.get("format_name")),
        duration_s=_seconds(file_format.get("duration")),
        videos=tuple(_video(entry) for entry in entries if entry.get("codec_type") == "video"),
        audios=tuple(
            _audio(order, entry)
            for order, entry in enumerate(e for e in entries if e.get("codec_type") == "audio")
        ),
    )


def _video(entry: Mapping[str, object]) -> VideoStream:
    return VideoStream(
        codec=_text(entry.get("codec_name")),
        width=_integer(entry.get("width", 0)),
        height=_integer(entry.get("height", 0)),
        sample_aspect_ratio=_optional_text(entry.get("sample_aspect_ratio")),
        frame_rate=_frame_rate(entry),
        rotation=_rotation(entry.get("side_data_list", [])),
        duration_s=_seconds(entry.get("duration")),
    )


def _audio(order: int, entry: Mapping[str, object]) -> AudioStream:
    return AudioStream(
        order=order,
        codec=_text(entry.get("codec_name")),
        sample_rate=int(_text(entry.get("sample_rate", "0"))),
        channels=_integer(entry.get("channels", 0)),
    )


def _frame_rate(entry: Mapping[str, object]) -> Fraction | None:
    """Média declarada; sem ela (`0/0`), a taxa-base (`r_frame_rate`)."""
    for key in ("avg_frame_rate", "r_frame_rate"):
        rate = _fraction(entry.get(key))
        if rate is not None:
            return rate
    return None


def _rotation(side_data: object) -> int:
    if not isinstance(side_data, list):
        raise TypeError("side_data_list não é lista")
    rotations = [
        _integer(item["rotation"]) for item in map(_mapping, side_data) if "rotation" in item
    ]
    return rotations[0] if rotations else 0


def _require_allowlisted(info: MediaInfo, container: Container) -> None:
    if info.format_name != _FORMAT_NAMES[container]:
        raise _unsupported("o contêiner não corresponde à assinatura do arquivo")
    if not info.videos:
        raise _unsupported("o arquivo não tem faixa de vídeo")
    video_codecs = {video.codec for video in info.videos}
    audio_codecs = {audio.codec for audio in info.audios}
    if not video_codecs <= _VIDEO_CODECS[container] or not audio_codecs <= _AUDIO_CODECS[container]:
        found = ", ".join(sorted(video_codecs | audio_codecs))
        raise _unsupported(f"codec fora da lista aceita para {container.name}: {found}")


def _checked_duration(info: MediaInfo, video: VideoStream, limits: Limits) -> float:
    duration = info.duration_s if info.duration_s is not None else video.duration_s
    if duration is None or duration <= 0:
        raise _unsupported("o arquivo não declara uma duração válida")
    longest = max(duration, video.duration_s or 0.0)
    if longest > limits.max_duration_s:
        raise MediaRejected(
            RejectionCode.MEDIA_TOO_LARGE,
            f"o vídeo dura mais de {limits.max_duration_s} s",
        )
    return duration


def _require_size_within(video: VideoStream, limits: Limits) -> None:
    if video.width <= 0 or video.height <= 0:
        raise _unsupported("o vídeo não declara largura e altura")
    long_side, short_side = max(video.width, video.height), min(video.width, video.height)
    if long_side > limits.max_long_side_px or short_side > limits.max_short_side_px:
        raise MediaRejected(
            RejectionCode.MEDIA_TOO_LARGE,
            f"{video.width} x {video.height} passa de "
            f"{limits.max_long_side_px} x {limits.max_short_side_px}",
        )


def _checked_frame_rate(video: VideoStream, limits: Limits) -> Fraction:
    if video.frame_rate is None:
        raise _unsupported("o vídeo não declara quadros por segundo")
    if video.frame_rate > limits.max_fps:
        raise MediaRejected(
            RejectionCode.MEDIA_TOO_LARGE, f"o vídeo passa de {limits.max_fps} quadros por segundo"
        )
    return video.frame_rate


def _require_square_pixels(video: VideoStream) -> None:
    if video.sample_aspect_ratio not in _SQUARE_PIXEL_RATIOS:
        raise _unsupported("o vídeo não usa pixels quadrados")


def _checked_rotation(video: VideoStream) -> int:
    if video.rotation % RIGHT_ANGLE:
        raise _unsupported("a orientação do vídeo não é de 0, 90, 180 ou 270 graus")
    return video.rotation % FULL_TURN


def _chosen_audio(audios: Sequence[AudioStream], params: Params, limits: Limits) -> AudioStream:
    order = params.audio_stream_index or 0
    if order >= len(audios):
        raise MediaRejected(
            RejectionCode.AUDIO_STREAM_NOT_FOUND,
            "o vídeo não tem faixa de áudio" if not audios else f"o vídeo não tem a faixa {order}",
        )
    chosen = audios[order]
    if chosen.sample_rate <= 0 or chosen.channels <= 0:
        raise _unsupported("a faixa de áudio não declara taxa e canais")
    if chosen.sample_rate > limits.max_sample_rate_hz or chosen.channels > limits.max_channels:
        raise MediaRejected(
            RejectionCode.AUDIO_LIMIT_EXCEEDED,
            f"{chosen.sample_rate} Hz e {chosen.channels} canais passam de "
            f"{limits.max_sample_rate_hz} Hz e {limits.max_channels} canais",
        )
    return chosen


def _unsupported(reason: str) -> MediaRejected:
    return MediaRejected(RejectionCode.UNSUPPORTED_FORMAT, reason)


def _mapping(raw: object) -> Mapping[str, object]:
    if not isinstance(raw, dict):
        raise TypeError("esperado um objeto JSON")
    return raw


def _text(raw: object) -> str:
    if not isinstance(raw, str):
        raise TypeError("esperado texto")
    return raw


def _optional_text(raw: object) -> str | None:
    return None if raw is None else _text(raw)


def _integer(raw: object) -> int:
    if isinstance(raw, int) and not isinstance(raw, bool):
        return raw
    raise TypeError("esperado inteiro")


def _seconds(raw: object) -> float | None:
    """Segundos como o ffprobe escreve ("300.000000"); "N/A" ou ausente é None."""
    if raw is None or raw == "N/A":
        return None
    seconds = float(_text(raw))
    if not math.isfinite(seconds):
        raise ValueError("duração não finita")
    return seconds


def _fraction(raw: object) -> Fraction | None:
    """ "30000/1001" → Fraction; "0/0" ou ausente é None."""
    if raw is None:
        return None
    numerator, _, denominator = _text(raw).partition("/")
    if int(denominator or "1") == 0:
        return None
    rate = Fraction(int(numerator), int(denominator or "1"))
    return rate if rate > 0 else None
