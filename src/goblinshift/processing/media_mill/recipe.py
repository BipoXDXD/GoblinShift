"""Receita media-mill, versões 1 e 2 (docs/GoblinShiftPlan.md, 1.5), sobre o contrato de 3.2.

Por vídeo: assinatura → ffprobe → allowlist e limites → áudio e miniaturas no rascunho → cópia
para `out/`. Tudo o que um vídeo produz fica no rascunho até o último passo dar certo: um vídeo
recusado no meio (miniatura de um trecho danificado depois de o áudio já ter saído) não deixa
saída órfã em `out/`, que a coleta do worker recusaria por não estar no relatório.

Um vídeo recusado vira `ItemRejected` e o lote continua (1.1). Cada vídeo tem prazo próprio
(`CallTimeouts.video_s`): passar dele recusa só aquele vídeo, e o resto do orçamento fica para os
outros. Esgotar o orçamento da execução é falha da execução inteira (`RESOURCE_LIMIT_EXCEEDED`), e
a cópia para `out/` fica fora da tradução de
erros: um `ENOSPC` ou `OUTPUT_LIMIT_EXCEEDED` derruba a execução em vez de virar rejeição.

As duas versões rodam o mesmo pipeline, `_Mill`, sobre um `MillPlan` (`plan.py`): um conserto de
segurança vale para as duas de uma vez. `MediaMillV1` e `MediaMillV2` só montam o plano. A v2
acrescenta o áudio com perda e o trecho (`trim_start_ms`/`trim_end_ms`), que vale para o áudio e
para as miniaturas (ADR 063).
"""

import math
import shutil
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

from goblinshift.processing.media_mill.commands import (
    AudioEncoding,
    Box,
    Source,
    audio_argv,
    encoding_for,
    last_frame_before_argv,
    probe_argv,
    read_log,
    thumbnail_argv,
)
from goblinshift.processing.media_mill.limits import MAX_INPUTS, Limits
from goblinshift.processing.media_mill.params import Params
from goblinshift.processing.media_mill.params_v2 import LOSSY, ParamsV2
from goblinshift.processing.media_mill.plan import MillPlan, plan_from_v1, plan_from_v2
from goblinshift.processing.media_mill.probe import (
    Inspected,
    inspect,
    parse_probe,
    require_audio_within_duration,
    require_dimensions_within,
    require_observed_frame_rate,
)
from goblinshift.processing.media_mill.rejection import MediaRejected, RejectionCode
from goblinshift.processing.media_mill.signature import HEADER_BYTES, detect_container
from goblinshift.processing.media_mill.tools import (
    CHILD_FILE_SIZE_BYTES,
    DEFAULT_BUDGET_S,
    MIB,
    Outcome,
    TimeBudget,
    Tools,
    ToolTimeoutError,
    budget_exhausted,
    run_tool,
)
from goblinshift.processing.names import safe_stem
from goblinshift.processing.previews import PendingPreview, process_each, report_with_previews
from goblinshift.processing.recipe import (
    DetailValue,
    InputFile,
    ItemNoticeCode,
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
# O áudio sai cortado, em amostras, na duração aceita mais esta margem: um quadro que começa depois
# da duração aceita recusa o vídeo, e a margem garante que, se a faixa passa dela, o corte ainda o
# registra.
AUDIO_CUT_MARGIN_S = 1.0
MS_PER_S = 1000
# 1.5: uma miniatura em 10% da duração; três em 10%, 50% e 90%.
THUMBNAIL_POINTS = {
    0: (),
    1: (Fraction(1, 10),),
    3: (Fraction(1, 10), Fraction(1, 2), Fraction(9, 10)),
}


@dataclass(frozen=True)
class CallTimeouts:
    """Teto de cada chamada e de cada vídeo (s); todos descontam também do orçamento da execução.

    Passar do teto da chamada recusa só aquele vídeo (`MEDIA_TOO_LARGE`): um GOP enorme, com um
    keyframe só em 300 s de 1080p60, faz a busca precisa da miniatura decodificar milhares de
    quadros. As chamadas de um vídeo somam até 440 s (ffprobe, áudio e, por miniatura, a busca e o
    último quadro anterior); `video_s` corta essa soma em 180 s, para um vídeo hostil deixar ao
    menos 300 s dos 480 s do orçamento (`DEFAULT_BUDGET_S`) para os outros (ADR 036). Esgotar o
    orçamento da execução falha a execução inteira (`RESOURCE_LIMIT_EXCEEDED`).
    Referência (Mac, 1 CPU, sem gVisor): ~100 quadros 1080p por segundo; com keyframe a cada
    10 s, cada miniatura decodifica no máximo 600 quadros.
    """

    probe_s: float = 20
    audio_s: float = 60
    thumbnail_s: float = 60
    video_s: float = 180


@dataclass(frozen=True)
class _Settings:
    limits: Limits
    max_inputs: int
    tools: Tools | None
    budget_s: float
    clock: Callable[[], float]
    timeouts: CallTimeouts


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
        self._settings = _Settings(
            limits or Limits(), max_inputs, tools, budget_s, clock, timeouts or CallTimeouts()
        )

    def run(
        self, params: Params, inputs: Sequence[InputFile], out: OutputDir, progress: Progress
    ) -> Report:
        return _mill_all(plan_from_v1(params), inputs, out, progress, self._settings)


class MediaMillV2:
    def __init__(
        self,
        limits: Limits | None = None,
        max_inputs: int = MAX_INPUTS,
        tools: Tools | None = None,
        budget_s: float = DEFAULT_BUDGET_S,
        clock: Callable[[], float] = time.monotonic,
        timeouts: CallTimeouts | None = None,
    ) -> None:
        self._settings = _Settings(
            limits or Limits(), max_inputs, tools, budget_s, clock, timeouts or CallTimeouts()
        )

    def run(
        self, params: ParamsV2, inputs: Sequence[InputFile], out: OutputDir, progress: Progress
    ) -> Report:
        return _mill_all(plan_from_v2(params), inputs, out, progress, self._settings)


def _mill_all(
    plan: MillPlan,
    inputs: Sequence[InputFile],
    out: OutputDir,
    progress: Progress,
    settings: _Settings,
) -> Report:
    """O pipeline das duas versões sobre o plano."""
    require_max_inputs(inputs, settings.max_inputs, "vídeos")
    tools = settings.tools or Tools.discover()
    budget = TimeBudget(settings.budget_s, settings.clock)
    # O diretório de rascunho vem de `tempfile.tempdir`, que o executor aponta para a área de
    # trabalho da tentativa (a raiz do container é somente leitura).
    with tempfile.TemporaryDirectory(prefix="media-mill-") as scratch:

        def mill_one(source: InputFile, pending: list[PendingPreview]) -> ItemOk | ItemRejected:
            work = Path(scratch, f"{source.position:04}")
            # Prazo próprio de cada vídeo, criado aqui: um vídeo hostil não gasta o dos outros.
            video = TimeBudget(settings.timeouts.video_s, settings.clock)
            runner = _Runner(tools, budget, settings.timeouts, work, video=video)
            return _Mill(runner, settings.limits, plan).process(source, out, pending)

        processed = process_each(inputs, mill_one, progress)
    details: dict[str, DetailValue] = {
        "audio_bit_depth": AUDIO_BIT_DEPTH,
        "thumbnail_max_side_px": settings.limits.thumbnail_max_side_px,
    }
    # Fora do `with`: os previews são gravados depois que o rascunho sai.
    return report_with_previews(processed, out, details=details)


@dataclass(frozen=True)
class _Window:
    """Trecho efetivo do vídeo, em segundos do contêiner."""

    start_s: float
    end_s: float
    # Sem fim pedido, ou fim ajustado à duração: o áudio é cortado na duração mais a margem e
    # conferido contra ela, como na v1.
    to_end: bool
    clamped: bool  # o fim pedido passava da duração


@dataclass(frozen=True)
class _Audio:
    path: Path
    encoding: AudioEncoding


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
    window: _Window
    audio: _Audio | None = None
    thumbnails: list[_Thumbnail] = field(default_factory=list)
    thumbnail_box: Box | None = None


class _Runner:
    """Roda as ferramentas de um vídeo no rascunho dele, com o teto da chamada, o prazo do vídeo e
    o orçamento da execução."""

    def __init__(
        self,
        tools: Tools,
        budget: TimeBudget,
        timeouts: CallTimeouts,
        work: Path,
        *,
        video: TimeBudget,
    ) -> None:
        self.tools = tools
        self.timeouts = timeouts
        self.work = work
        self._budget = budget
        self._video = video

    def run(self, argv: Sequence[str], *, cap_s: float, name: str) -> Outcome:
        video_remaining = self._video.remaining()
        if video_remaining <= 0:
            raise self._video_too_long()
        limit = min(cap_s, video_remaining)
        timeout_s = self._budget.timeout(limit)
        try:
            outcome = run_tool(argv, timeout_s=timeout_s, scratch=self.work, name=name)
        except ToolTimeoutError:
            # Quem cortou o prazo decide: o orçamento da execução, o prazo do vídeo ou a etapa.
            if timeout_s < limit:
                raise budget_exhausted() from None
            if limit < cap_s:
                raise self._video_too_long() from None
            raise MediaRejected(
                RejectionCode.MEDIA_TOO_LARGE,
                f"processar este vídeo passou de {cap_s:g} s numa etapa ({name})",
            ) from None
        # Saída ou log acima do `RLIMIT_FSIZE` (milhões de quadros minúsculos no `showinfo`) é
        # tamanho, não dano: a recusa vem antes de quem chamou olhar `succeeded`.
        if outcome.file_size_exceeded:
            raise MediaRejected(
                RejectionCode.MEDIA_TOO_LARGE,
                f"processar este vídeo gravou mais de {CHILD_FILE_SIZE_BYTES // MIB} MiB "
                f"numa etapa ({name})",
            )
        return outcome

    def _video_too_long(self) -> MediaRejected:
        return MediaRejected(
            RejectionCode.MEDIA_TOO_LARGE,
            f"processar este vídeo passou de {self.timeouts.video_s:g} s no total",
        )


class _Mill:
    """Um vídeo: inspeção e conversões no rascunho próprio, depois a cópia para `out/`."""

    def __init__(self, runner: _Runner, limits: Limits, plan: MillPlan) -> None:
        self._runner = runner
        self._tools = runner.tools
        self._timeouts = runner.timeouts
        self._work = runner.work
        self._limits = limits
        self._plan = plan

    def process(
        self, source: InputFile, out: OutputDir, pending: list[PendingPreview]
    ) -> ItemOk | ItemRejected:
        self._work.mkdir()
        try:
            try:
                converted = self._convert(source)
            except MediaRejected as rejection:
                return ItemRejected(source.position, rejection.code, rejection.reason)
            return _publish(converted, source, self._plan, out, pending)
        finally:
            shutil.rmtree(self._work)

    def _convert(self, source: InputFile) -> _Converted:
        media_source = Source(source.path, detect_container(_header(source.path)))
        media = self._inspect(media_source)
        converted = _Converted(media, _window(self._plan, media))
        if media.audio is not None:
            converted.audio = self._extract_audio(media_source, media, converted.window)
        points = THUMBNAIL_POINTS[self._plan.thumbnail_count]
        if points:
            converted.thumbnail_box = _fit(media, self._limits.thumbnail_max_side_px)
            span_s = self._thumbnail_span_s(media, converted.window)
            converted.thumbnails = [
                self._thumbnail(
                    media_source,
                    point,
                    converted.window.start_s + float(point) * span_s,
                    converted.thumbnail_box,
                )
                for point in points
            ]
        return converted

    def _thumbnail_span_s(self, media: Inspected, window: _Window) -> float:
        """Do início do trecho ao fim dele na faixa de vídeo; sem quadro no trecho, 0.

        Os instantes das miniaturas são `início + p·(fim_vídeo - início)`. Sem fim pedido, o fim é
        o da faixa de vídeo, como na v1. Com o trecho começando depois do último quadro, os três
        alvos caem no início e a miniatura usa o último quadro anterior, com aviso.
        """
        video_end_s = media.video_duration_s
        if self._plan.trim_end_ms is not None:
            video_end_s = min(window.end_s, video_end_s)
        return max(0.0, video_end_s - window.start_s)

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
        return inspect(parse_probe(data), source.container, self._plan, self._limits)

    def _extract_audio(self, source: Source, media: Inspected, window: _Window) -> _Audio:
        audio = media.audio
        if audio is None:
            raise ValueError("extração sem faixa de áudio escolhida")
        rate = audio.sample_rate
        encoding = encoding_for(self._plan.audio_format, self._plan.audio_bitrate_kbps, rate)
        target = self._work / f"audio{encoding.extension}"
        # Inteiros calculados de valores já validados (duração e taxa do probe, trecho em ms
        # inteiros), nunca de texto. As amostras contam na taxa da fonte, antes da reamostragem.
        start_sample = self._plan.trim_start_ms * rate // MS_PER_S
        cut_samples = math.ceil((media.duration_s + AUDIO_CUT_MARGIN_S) * rate)
        if not window.to_end and self._plan.trim_end_ms is not None:
            cut_samples = min(-(-self._plan.trim_end_ms * rate // MS_PER_S), cut_samples)
        argv = audio_argv(
            self._tools.ffmpeg,
            source,
            stream_order=audio.order,
            channels=audio.channels,
            encoding=encoding,
            start_sample=start_sample,
            cut_samples=cut_samples,
            target=target,
        )
        outcome = self._runner.run(argv, cap_s=self._timeouts.audio_s, name="audio")
        log = read_log(outcome.log)
        # Faixa que acaba antes do trecho: nada passa pelo corte, e isso não é dano.
        if start_sample > 0 and log.audio_samples == 0 and not log.has_errors:
            raise MediaRejected(
                RejectionCode.TRIM_OUT_OF_RANGE, "o trecho começa depois do fim da faixa de áudio"
            )
        if not outcome.succeeded or log.has_errors or not _non_empty(target):
            raise _damaged("a faixa de áudio está danificada ou truncada")
        if window.to_end:
            # Dano antes de tamanho: o corte termina a leitura, e o que ela leu já é a faixa toda
            # ou passa da duração (sucesso parcial nunca). O `asetpts` zera os instantes no início
            # do trecho, então a conta é contra o que resta da duração.
            require_audio_within_duration(
                media.duration_s - window.start_s,
                last_start_s=log.last_audio_s,
                last_start_by_samples_s=log.last_audio_by_samples_s,
                samples=log.audio_samples,
                cut_samples=cut_samples - start_sample,
            )
        return _Audio(target, encoding)

    def _thumbnail(
        self, source: Source, point: Fraction, requested_s: float, box: Box
    ) -> _Thumbnail:
        label = str(int(point * 100))
        at_target = self._work / f"thumbnail-{label}.jpg"
        outcome = self._runner.run(
            thumbnail_argv(self._tools.ffmpeg, source, at_s=requested_s, box=box, target=at_target),
            cap_s=self._timeouts.thumbnail_s,
            name=f"thumbnail-{label}",
        )
        log = read_log(outcome.log)
        # O cabeçalho pode declarar 320x240 e trocar para 4K no meio: vale o quadro decodificado.
        require_dimensions_within(log.max_long_side_px, log.max_short_side_px, self._limits)
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
        require_dimensions_within(log.max_long_side_px, log.max_short_side_px, self._limits)
        # Esta leitura decodifica do início até o alvo: é onde a taxa declarada pode mentir.
        require_observed_frame_rate(log.frame_count, log.frame_span_s, self._limits)
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
    plan: MillPlan,
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
        name = stem + converted.audio.encoding.extension
        outputs.append(_copy(converted.audio.path, out, name, source.position, OutputGroup.AUDIO))
    for thumbnail in converted.thumbnails:
        name = f"{stem}-{thumbnail.label}pct.jpg"
        copied = _copy(thumbnail.path, out, name, source.position, OutputGroup.THUMBNAILS)
        outputs.append(copied)
        pending.append(PendingPreview(source.position, copied, name, thumbnail.path.read_bytes()))
    return ItemOk(
        position=source.position,
        outputs=tuple(outputs),
        warnings=tuple(_warning(t) for t in converted.thumbnails if t.before_target),
        details=_details(converted, plan),
        notices=_notices(converted),
    )


def _copy(path: Path, out: OutputDir, name: str, position: int, group: OutputGroup) -> str:
    output = out.create(name, OutputRole.CONTENT, position, group)
    with path.open("rb") as source, output.stream as target:
        shutil.copyfileobj(source, target, COPY_CHUNK_BYTES)
    return output.name


def _details(converted: _Converted, plan: MillPlan) -> dict[str, DetailValue]:
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
            "audio_format": plan.audio_format.value,
        }
    if converted.thumbnail_box is not None:
        details |= {
            "thumbnail_width": converted.thumbnail_box.width,
            "thumbnail_height": converted.thumbnail_box.height,
        }
    for thumbnail in converted.thumbnails:
        details[f"thumbnail_{thumbnail.label}_requested_ms"] = _ms(thumbnail.requested_s)
        details[f"thumbnail_{thumbnail.label}_effective_ms"] = _ms(thumbnail.effective_s)
    if plan.v2_details:
        details |= _v2_details(converted, plan)
    return details


def _v2_details(converted: _Converted, plan: MillPlan) -> dict[str, DetailValue]:
    """Os campos que só a v2 registra: taxa de saída, bitrate (com perda) e trecho efetivo."""
    details: dict[str, DetailValue] = {}
    if converted.audio is not None:
        details["audio_sample_rate_out_hz"] = converted.audio.encoding.sample_rate_hz
        if plan.audio_format in LOSSY:
            details["audio_bitrate_kbps"] = plan.audio_bitrate_kbps
    if plan.trimmed:
        details["trim_start_ms"] = plan.trim_start_ms
        details["trim_end_ms"] = _ms(converted.window.end_s)
    return details


def _notices(converted: _Converted) -> tuple[ItemNoticeCode, ...]:
    notices: list[ItemNoticeCode] = []
    audio, chosen = converted.audio, converted.media.audio
    if (
        audio is not None
        and chosen is not None
        and audio.encoding.sample_rate_hz != chosen.sample_rate
    ):
        notices.append(ItemNoticeCode.AUDIO_RESAMPLED)
    if converted.window.clamped:
        notices.append(ItemNoticeCode.MEDIA_TRIM_CLAMPED)
    return tuple(notices)


def _window(plan: MillPlan, media: Inspected) -> _Window:
    """`[início, min(fim, duração do contêiner)]`; início no fim ou depois dele recusa o vídeo."""
    duration_s = media.duration_s
    start_s = plan.trim_start_ms / MS_PER_S
    if start_s >= duration_s:
        raise MediaRejected(
            RejectionCode.TRIM_OUT_OF_RANGE,
            f"o trecho começa em {start_s:.3f} s, e o vídeo dura {duration_s:.3f} s",
        )
    if plan.trim_end_ms is None:
        return _Window(start_s, duration_s, to_end=True, clamped=False)
    end_s = plan.trim_end_ms / MS_PER_S
    if end_s > duration_s:
        return _Window(start_s, duration_s, to_end=True, clamped=True)
    return _Window(start_s, end_s, to_end=False, clamped=False)


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
