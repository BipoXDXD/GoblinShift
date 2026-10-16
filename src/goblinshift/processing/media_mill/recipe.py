"""Receita media-mill, versão 1 (docs/GoblinShiftPlan.md, 1.5), sobre o contrato de 3.2.

Por vídeo: assinatura → ffprobe → allowlist e limites → áudio e miniaturas no rascunho → cópia
para `out/`. Tudo o que um vídeo produz fica no rascunho até o último passo dar certo: um vídeo
recusado no meio (miniatura de um trecho danificado depois de o áudio já ter saído) não deixa
saída órfã em `out/`, que a coleta do worker recusaria por não estar no relatório.

Um vídeo recusado vira `ItemRejected` e o lote continua (1.1). Estourar o tempo é falha da
execução inteira (`RESOURCE_LIMIT_EXCEEDED`), e a cópia para `out/` fica fora da tradução de
erros: um `ENOSPC` ou `OUTPUT_LIMIT_EXCEEDED` derruba a execução em vez de virar rejeição.
"""

import shutil
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

from goblinshift.processing.media_mill.commands import (
    Box,
    Source,
    audio_argv,
    last_frame_before_argv,
    probe_argv,
    read_log,
    thumbnail_argv,
)
from goblinshift.processing.media_mill.limits import MAX_INPUTS, Limits
from goblinshift.processing.media_mill.params import AudioFormat, Params
from goblinshift.processing.media_mill.probe import Inspected, inspect, parse_probe
from goblinshift.processing.media_mill.rejection import MediaRejected, RejectionCode
from goblinshift.processing.media_mill.signature import HEADER_BYTES, detect_container
from goblinshift.processing.media_mill.tools import (
    DEFAULT_BUDGET_S,
    Outcome,
    TimeBudget,
    Tools,
    ToolTimeoutError,
    budget_exhausted,
    run_tool,
)
from goblinshift.processing.names import safe_stem
from goblinshift.processing.previews import PendingPreview, attach_previews, write_previews
from goblinshift.processing.recipe import (
    DetailValue,
    InputFile,
    ItemOk,
    ItemRejected,
    ItemWarningCode,
    OutputDir,
    OutputGroup,
    OutputRole,
    Progress,
    Report,
    ReportWarning,
    require_max_inputs,
)

FALLBACK_STEM = "video"
AUDIO_BIT_DEPTH = 16
COPY_CHUNK_BYTES = 1024 * 1024
# A saída do ffprobe descreve no máximo 16 faixas: alguns KiB. Acima disto, não é vídeo comum.
PROBE_OUTPUT_MAX_BYTES = 1024 * 1024
MS_PER_S = 1000
# 1.5: uma miniatura em 10% da duração; três em 10%, 50% e 90%.
THUMBNAIL_POINTS = {
    0: (),
    1: (Fraction(1, 10),),
    3: (Fraction(1, 10), Fraction(1, 2), Fraction(9, 10)),
}
_AUDIO_EXTENSIONS = {AudioFormat.WAV: ".wav", AudioFormat.FLAC: ".flac"}


@dataclass(frozen=True)
class CallTimeouts:
    """Teto de cada chamada (s); todas descontam também do orçamento da execução (TimeBudget).

    Passar do teto da chamada recusa só aquele vídeo (`MEDIA_TOO_LARGE`): um GOP enorme, com um
    keyframe só em 300 s de 1080p60, faz a busca precisa da miniatura decodificar milhares de
    quadros. Esgotar o orçamento da execução falha a execução inteira (`RESOURCE_LIMIT_EXCEEDED`).
    Referência (Mac, 1 CPU, sem gVisor): ~100 quadros 1080p por segundo; com keyframe a cada
    10 s, cada miniatura decodifica no máximo 600 quadros.
    """

    probe_s: float = 20
    audio_s: float = 60
    thumbnail_s: float = 60


class MediaMillV1:
    def __init__(
        self,
        limits: Limits | None = None,
        max_inputs: int = MAX_INPUTS,
        tools: Tools | None = None,
        budget_s: float = DEFAULT_BUDGET_S,
        clock: Callable[[], float] = time.monotonic,
        timeouts: CallTimeouts | None = None,
    ) -> None:
        self._timeouts = timeouts or CallTimeouts()
        self._limits = limits or Limits()
        self._max_inputs = max_inputs
        self._tools = tools
        self._budget_s = budget_s
        self._clock = clock

    def run(
        self, params: Params, inputs: Sequence[InputFile], out: OutputDir, progress: Progress
    ) -> Report:
        require_max_inputs(inputs, self._max_inputs, "vídeos")
        tools = self._tools or Tools.discover()
        budget = TimeBudget(self._budget_s, self._clock)
        items: list[ItemOk | ItemRejected] = []
        pending: list[PendingPreview] = []
        # O diretório de rascunho vem de `tempfile.tempdir`, que o executor aponta para a área de
        # trabalho da tentativa (a raiz do container é somente leitura).
        with tempfile.TemporaryDirectory(prefix="media-mill-") as scratch:
            for source in inputs:
                work = Path(scratch, f"{source.position:04}")
                mill = _Mill(_Runner(tools, budget, self._timeouts, work), self._limits, params)
                items.append(mill.process(source, out, pending))
                progress.item_done(source.position)
        details: dict[str, DetailValue] = {
            "audio_bit_depth": AUDIO_BIT_DEPTH,
            "thumbnail_max_side_px": self._limits.thumbnail_max_side_px,
        }
        return Report(attach_previews(items, write_previews(out, pending)), details=details)


@dataclass(frozen=True)
class _Thumbnail:
    label: str  # "10", "50" ou "90" (por cento da duração)
    path: Path
    requested_s: float
    effective_s: float
    before_target: bool  # nenhum quadro em ou depois do alvo: usado o último anterior


@dataclass
class _Converted:
    media: Inspected
    audio: Path | None = None
    thumbnails: list[_Thumbnail] = field(default_factory=list)
    thumbnail_box: Box | None = None


class _Runner:
    """Roda as ferramentas de um vídeo no rascunho dele, com o teto da chamada e o orçamento."""

    def __init__(
        self, tools: Tools, budget: TimeBudget, timeouts: CallTimeouts, work: Path
    ) -> None:
        self.tools = tools
        self.timeouts = timeouts
        self.work = work
        self._budget = budget

    def run(self, argv: Sequence[str], *, cap_s: float, name: str) -> Outcome:
        timeout_s = self._budget.timeout(cap_s)
        try:
            return run_tool(argv, timeout_s=timeout_s, scratch=self.work, name=name)
        except ToolTimeoutError:
            if timeout_s < cap_s:
                raise budget_exhausted() from None
            raise MediaRejected(
                RejectionCode.MEDIA_TOO_LARGE,
                f"processar este vídeo passou de {cap_s:g} s numa etapa ({name})",
            ) from None


class _Mill:
    """Um vídeo: inspeção e conversões no rascunho próprio, depois a cópia para `out/`."""

    def __init__(self, runner: _Runner, limits: Limits, params: Params) -> None:
        self._runner = runner
        self._tools = runner.tools
        self._timeouts = runner.timeouts
        self._work = runner.work
        self._limits = limits
        self._params = params

    def process(
        self, source: InputFile, out: OutputDir, pending: list[PendingPreview]
    ) -> ItemOk | ItemRejected:
        self._work.mkdir()
        try:
            try:
                converted = self._convert(source)
            except MediaRejected as rejection:
                return ItemRejected(source.position, rejection.code, rejection.reason)
            return _publish(converted, source, self._params, out, pending)
        finally:
            shutil.rmtree(self._work)

    def _convert(self, source: InputFile) -> _Converted:
        media_source = Source(source.path, detect_container(_header(source.path)))
        converted = _Converted(self._inspect(media_source))
        if converted.media.audio is not None:
            converted.audio = self._extract_audio(media_source, converted.media)
        points = THUMBNAIL_POINTS[self._params.thumbnail_count]
        if points:
            converted.thumbnail_box = _fit(converted.media, self._limits.thumbnail_max_side_px)
            converted.thumbnails = [
                self._thumbnail(media_source, converted.media, point, converted.thumbnail_box)
                for point in points
            ]
        return converted

    def _inspect(self, source: Source) -> Inspected:
        outcome = self._runner.run(
            probe_argv(self._tools.ffprobe, source), cap_s=self._timeouts.probe_s, name="probe"
        )
        if not outcome.succeeded:
            raise _damaged("o ffprobe não conseguiu ler o arquivo")
        with outcome.stdout.open("rb") as stdout:
            data = stdout.read(PROBE_OUTPUT_MAX_BYTES + 1)
        if len(data) > PROBE_OUTPUT_MAX_BYTES:
            raise _damaged("a descrição do arquivo é grande demais para um vídeo")
        return inspect(parse_probe(data), source.container, self._params, self._limits)

    def _extract_audio(self, source: Source, media: Inspected) -> Path:
        audio = media.audio
        if audio is None:
            raise ValueError("extração sem faixa de áudio escolhida")
        target = self._work / f"audio{_AUDIO_EXTENSIONS[self._params.audio_format]}"
        argv = audio_argv(
            self._tools.ffmpeg,
            source,
            stream_order=audio.order,
            sample_rate=audio.sample_rate,
            channels=audio.channels,
            audio_format=self._params.audio_format,
            target=target,
        )
        outcome = self._runner.run(argv, cap_s=self._timeouts.audio_s, name="audio")
        if not outcome.succeeded or read_log(outcome.log).has_errors or not _non_empty(target):
            raise _damaged("a faixa de áudio está danificada ou truncada")
        return target

    def _thumbnail(self, source: Source, media: Inspected, point: Fraction, box: Box) -> _Thumbnail:
        label = str(int(point * 100))
        requested_s = float(point) * media.video_duration_s
        at_target = self._work / f"thumbnail-{label}.jpg"
        outcome = self._runner.run(
            thumbnail_argv(self._tools.ffmpeg, source, at_s=requested_s, box=box, target=at_target),
            cap_s=self._timeouts.thumbnail_s,
            name=f"thumbnail-{label}",
        )
        log = read_log(outcome.log)
        if outcome.succeeded and log.first_frame_s is not None and _non_empty(at_target):
            if log.has_errors:
                raise _damaged("o vídeo está danificado perto do ponto da miniatura")
            return _Thumbnail(label, at_target, requested_s, requested_s + log.first_frame_s, False)
        return self._last_frame_before(source, label, requested_s, box)

    def _last_frame_before(
        self, source: Source, label: str, requested_s: float, box: Box
    ) -> _Thumbnail:
        """1.5: sem quadro em ou depois do alvo, o último anterior, com aviso."""
        target = self._work / f"thumbnail-{label}-before.jpg"
        argv = last_frame_before_argv(
            self._tools.ffmpeg, source, until_s=requested_s, box=box, target=target
        )
        outcome = self._runner.run(
            argv, cap_s=self._timeouts.thumbnail_s, name=f"thumbnail-{label}-before"
        )
        log = read_log(outcome.log)
        if (
            not outcome.succeeded
            or log.has_errors
            or log.last_frame_s is None
            or not _non_empty(target)
        ):
            raise _damaged("o vídeo não tem quadro legível para a miniatura")
        return _Thumbnail(label, target, requested_s, log.last_frame_s, True)


def _publish(
    converted: _Converted,
    source: InputFile,
    params: Params,
    out: OutputDir,
    pending: list[PendingPreview],
) -> ItemOk:
    """Copia as saídas para `out/` e guarda, para depois de todo o conteúdo, os previews.

    O preview de cada miniatura é ela mesma (2.2, "mídia por miniaturas"): já é um JPEG de até
    640 px, e reencodar pediria outra chamada ao FFmpeg. Os bytes saem do rascunho antes de ele ser
    apagado; a que passar do teto de JPEG de preview fica sem preview (`write_previews`).
    """
    stem = safe_stem(source.original_name, FALLBACK_STEM)
    outputs: list[str] = []
    if converted.audio is not None:
        name = stem + _AUDIO_EXTENSIONS[params.audio_format]
        outputs.append(_copy(converted.audio, out, name, source.position, OutputGroup.AUDIO))
    for thumbnail in converted.thumbnails:
        name = f"{stem}-{thumbnail.label}pct.jpg"
        copied = _copy(thumbnail.path, out, name, source.position, OutputGroup.THUMBNAILS)
        outputs.append(copied)
        pending.append(PendingPreview(source.position, copied, name, thumbnail.path.read_bytes()))
    return ItemOk(
        position=source.position,
        outputs=tuple(outputs),
        warnings=tuple(_warning(t) for t in converted.thumbnails if t.before_target),
        details=_details(converted, params),
    )


def _copy(path: Path, out: OutputDir, name: str, position: int, group: OutputGroup) -> str:
    output = out.create(name, OutputRole.CONTENT, position, group)
    with path.open("rb") as source, output.stream as target:
        shutil.copyfileobj(source, target, COPY_CHUNK_BYTES)
    return output.name


def _details(converted: _Converted, params: Params) -> dict[str, DetailValue]:
    media = converted.media
    details: dict[str, DetailValue] = {
        "container": media.container.name.lower(),
        "video_codec": media.codec,
        "width": media.display_width,
        "height": media.display_height,
        "rotation": media.rotation,
        "frame_rate": str(media.frame_rate),
        "duration_ms": _ms(media.duration_s),
    }
    if media.audio is not None:
        details |= {
            "audio_stream_index": media.audio.order,
            "audio_codec": media.audio.codec,
            "audio_sample_rate_hz": media.audio.sample_rate,
            "audio_channels": media.audio.channels,
            "audio_format": params.audio_format.value,
        }
    if converted.thumbnail_box is not None:
        details |= {
            "thumbnail_width": converted.thumbnail_box.width,
            "thumbnail_height": converted.thumbnail_box.height,
        }
    for thumbnail in converted.thumbnails:
        details[f"thumbnail_{thumbnail.label}_requested_ms"] = _ms(thumbnail.requested_s)
        details[f"thumbnail_{thumbnail.label}_effective_ms"] = _ms(thumbnail.effective_s)
    return details


def _warning(thumbnail: _Thumbnail) -> ReportWarning:
    return ReportWarning(
        ItemWarningCode.THUMBNAIL_BEFORE_TARGET,
        f"miniatura de {thumbnail.label}%: nenhum quadro em ou depois de "
        f"{thumbnail.requested_s:.3f} s; usado o último anterior, em {thumbnail.effective_s:.3f} s",
    )


def _fit(media: Inspected, max_side: int) -> Box:
    """Cabe em `max_side` x `max_side` sem ampliar; trunca, como o Image Forge (2.3)."""
    width, height = media.display_width, media.display_height
    longest = max(width, height)
    if longest <= max_side:
        return Box(width, height)
    return Box(max(1, width * max_side // longest), max(1, height * max_side // longest))


def _header(path: Path) -> bytes:
    with path.open("rb") as source:
        return source.read(HEADER_BYTES)


def _non_empty(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > 0


def _ms(seconds: float) -> int:
    return round(seconds * MS_PER_S)


def _damaged(reason: str) -> MediaRejected:
    return MediaRejected(RejectionCode.UNSUPPORTED_FORMAT, reason)
