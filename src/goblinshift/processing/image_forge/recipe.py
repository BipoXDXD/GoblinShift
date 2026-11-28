"""Receita image-forge, versões 1 e 2 (docs/GoblinShiftPlan.md, 1.2), sobre o contrato de 3.2.

Por arquivo: preparar (validar, decodificar, orientar, modo, cor) → ajustar (recorte,
redimensionamento e margem) → gravar → miniatura. Um arquivo recusado vira `ItemRejected` e o lote
continua (1.1). A gravação fica fora da tradução de erros do decodificador: um `ENOSPC` ou
`OUTPUT_LIMIT_EXCEEDED` derruba a execução em vez de virar rejeição.

As duas versões rodam o mesmo pipeline, `forge`, sobre um `ForgePlan` (`plan.py`): um conserto de
segurança vale para as duas de uma vez. `ImageForgeV1` e `ImageForgeV2` só montam o plano.

O relatório registra fatos, não avisos (ambiguidade A2): perfil assumido ou convertido e quadros
MPO descartados ficam em `details`, sem mudar o estado para `succeeded_with_warnings`.
"""

import io
from collections.abc import Sequence
from dataclasses import dataclass

from PIL import Image

from goblinshift.processing.image_forge.color import ColorProfile
from goblinshift.processing.image_forge.encoding import SaveFormat, encode
from goblinshift.processing.image_forge.geometry import FitMode, Layout, fit_size, layout
from goblinshift.processing.image_forge.limits import MAX_INPUTS, Limits
from goblinshift.processing.image_forge.params import WHITE, Params
from goblinshift.processing.image_forge.params_v2 import OutputFormatV2, ParamsV2
from goblinshift.processing.image_forge.plan import ForgePlan, plan_from_v1, plan_from_v2
from goblinshift.processing.image_forge.preparation import Prepared, prepare
from goblinshift.processing.image_forge.rejection import ImageRejected
from goblinshift.processing.names import safe_stem
from goblinshift.processing.previews import PendingPreview, process_each, report_with_previews
from goblinshift.processing.recipe import (
    DetailValue,
    InputFile,
    ItemNoticeCode,
    ItemOk,
    ItemRejected,
    OutputDir,
    OutputGroup,
    OutputRole,
    Progress,
    Report,
    require_max_inputs,
)

FALLBACK_STEM = "imagem"
RESAMPLING = Image.Resampling.LANCZOS
RESAMPLING_NAME = "lanczos"
# Preview (2.2): JPEG que cabe em 512 x 512, sem ampliar, com fundo branco onde havia alfa.
PREVIEW_BOX = 512
PREVIEW_JPEG_QUALITY = 80
PREVIEW_EXTENSION = ".jpg"
# Margem do `pad`: transparente; no JPEG, o `jpeg_background` a cobre ao compor o alfa.
_WITH_ALPHA = {"RGB": "RGBA", "L": "LA", "RGBA": "RGBA", "LA": "LA"}


class ImageForgeV1:
    def __init__(self, limits: Limits | None = None, max_inputs: int = MAX_INPUTS) -> None:
        self._limits = limits or Limits()
        self._max_inputs = max_inputs

    def run(
        self, params: Params, inputs: Sequence[InputFile], out: OutputDir, progress: Progress
    ) -> Report:
        return forge(
            plan_from_v1(params),
            inputs,
            out,
            progress,
            limits=self._limits,
            max_inputs=self._max_inputs,
        )


class ImageForgeV2:
    def __init__(self, limits: Limits | None = None, max_inputs: int = MAX_INPUTS) -> None:
        self._limits = limits or Limits()
        self._max_inputs = max_inputs

    def run(
        self, params: ParamsV2, inputs: Sequence[InputFile], out: OutputDir, progress: Progress
    ) -> Report:
        return forge(
            plan_from_v2(params),
            inputs,
            out,
            progress,
            limits=self._limits,
            max_inputs=self._max_inputs,
        )


def forge(
    plan: ForgePlan,
    inputs: Sequence[InputFile],
    out: OutputDir,
    progress: Progress,
    *,
    limits: Limits,
    max_inputs: int,
) -> Report:
    """O pipeline das duas versões sobre o plano."""
    require_max_inputs(inputs, max_inputs, "imagens")
    processed = process_each(
        inputs, lambda source, pending: _forge_one(source, plan, limits, out, pending), progress
    )
    details: dict[str, DetailValue] = {
        "resampling": RESAMPLING_NAME,
        "thumbnail_box": limits.thumbnail_box,
    }
    return report_with_previews(processed, out, details=details)


def _forge_one(
    source: InputFile,
    plan: ForgePlan,
    limits: Limits,
    out: OutputDir,
    pending: list[PendingPreview],
) -> ItemOk | ItemRejected:
    try:
        prepared = prepare(source.path, limits, plan.formats)
    except ImageRejected as rejection:
        return ItemRejected(source.position, rejection.code, rejection.reason)
    writer = _Writer(
        out,
        source.position,
        safe_stem(source.original_name, FALLBACK_STEM),
        _save_format(plan.output, prepared.source_format),
        plan,
    )
    plan_layout = layout(
        prepared.image.width,
        prepared.image.height,
        fit=plan.fit,
        box=plan.box,
        scale_percent=plan.scale_percent,
    )
    resized = _apply(prepared.image, plan_layout)
    outputs = [writer.write(resized, OutputGroup.IMAGES)]
    # Guardado em memória (no máximo 512 x 512) e gravado depois de todo o conteúdo.
    pending.append(
        PendingPreview(
            source.position, outputs[0], writer.stem + PREVIEW_EXTENSION, _preview(resized)
        )
    )
    if plan.make_thumbnails:
        box = limits.thumbnail_box
        outputs.append(writer.write(_fit(resized, box, box), OutputGroup.THUMBNAILS))
    details = _details(source, prepared, resized, writer.save_format)
    if plan.fit_details:
        details |= _fit_details(plan, plan_layout)
    return ItemOk(
        position=source.position,
        outputs=tuple(outputs),
        details=details,
        notices=_notices(prepared),
    )


@dataclass(frozen=True)
class _Writer:
    """Grava as saídas de uma entrada: mesmo radical e formato em `images/` e `thumbnails/`."""

    out: OutputDir
    position: int
    stem: str
    save_format: SaveFormat
    plan: ForgePlan

    def write(self, image: Image.Image, group: OutputGroup) -> str:
        output = self.out.create(
            self.stem + self.save_format.extension, OutputRole.CONTENT, self.position, group
        )
        with output.stream as stream:
            encode(
                image,
                self.save_format,
                stream,
                jpeg_quality=self.plan.jpeg_quality,
                jpeg_background=self.plan.jpeg_background,
                webp_quality=self.plan.webp_quality,
                webp_lossless=self.plan.webp_lossless,
            )
        return output.name


def _preview(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    encode(
        _fit(image, PREVIEW_BOX, PREVIEW_BOX),
        SaveFormat.JPEG,
        buffer,
        jpeg_quality=PREVIEW_JPEG_QUALITY,
        jpeg_background=WHITE,
    )
    return buffer.getvalue()


def _notices(prepared: Prepared) -> tuple[ItemNoticeCode, ...]:
    if prepared.color_profile is ColorProfile.DISPLAY_P3_CONVERTED:
        return (ItemNoticeCode.IMAGE_P3_CONVERTED,)
    return ()


def _save_format(requested: OutputFormatV2, source_format: str) -> SaveFormat:
    """`preserve`: PNG e GIF saem PNG; WebP continua WebP; JPEG e MPO saem JPEG (A3)."""
    match requested:
        case OutputFormatV2.JPEG:
            return SaveFormat.JPEG
        case OutputFormatV2.PNG:
            return SaveFormat.PNG
        case OutputFormatV2.WEBP:
            return SaveFormat.WEBP
        case OutputFormatV2.PRESERVE:
            return _PRESERVED.get(source_format, SaveFormat.JPEG)


_PRESERVED = {"png": SaveFormat.PNG, "gif": SaveFormat.PNG, "webp": SaveFormat.WEBP}


def _apply(image: Image.Image, plan_layout: Layout) -> Image.Image:
    """Recorte, depois redimensionamento, depois a margem do `pad`, se houver.

    O recorte vem antes de redimensionar: o `resize(box=...)` do Pillow lê pixels de fora da caixa
    para o filtro, e a cor das bordas cortadas vazaria para dentro do `cover`.
    """
    x, y, width, height = plan_layout.crop
    content = image
    if (x, y, width, height) != (0, 0, image.width, image.height):
        content = image.crop((x, y, x + width, y + height))
    if plan_layout.content != content.size:
        content = content.resize(plan_layout.content, RESAMPLING)
    if plan_layout.canvas == plan_layout.content:
        return content
    mode = _WITH_ALPHA[content.mode]
    canvas = Image.new(mode, plan_layout.canvas)  # zeros: transparente
    canvas.paste(content.convert(mode), plan_layout.offset)
    return canvas


def _fit_details(plan: ForgePlan, plan_layout: Layout) -> dict[str, DetailValue]:
    """O ajuste da v2: o modo e, conforme ele, a escala, o recorte ou o tamanho do conteúdo."""
    details: dict[str, DetailValue] = {"fit": plan.fit.value}
    match plan.fit:
        case FitMode.CONTAIN:
            pass
        case FitMode.SCALE:
            details["scale_percent"] = plan.scale_percent
        case FitMode.COVER:
            x, y, width, height = plan_layout.crop
            details |= {"crop_x": x, "crop_y": y, "crop_width": width, "crop_height": height}
        case FitMode.PAD:
            details["content_width"], details["content_height"] = plan_layout.content
    return details


def _fit(image: Image.Image, max_width: int, max_height: int) -> Image.Image:
    size = fit_size(image.width, image.height, max_width, max_height)
    return image if size == image.size else image.resize(size, RESAMPLING)


def _details(
    source: InputFile, prepared: Prepared, resized: Image.Image, save_format: SaveFormat
) -> dict[str, DetailValue]:
    """Dimensões "antes" já orientadas: são as que o usuário vê e as que entram na escala."""
    return {
        "width_before": prepared.image.width,
        "height_before": prepared.image.height,
        "bytes_before": source.size,
        "format_before": prepared.source_format,
        "width_after": resized.width,
        "height_after": resized.height,
        "format_after": save_format.value,
        "color_profile": prepared.color_profile.value,
        "orientation_applied": prepared.orientation,
        "mpo_frames_discarded": prepared.frames_discarded,
    }
