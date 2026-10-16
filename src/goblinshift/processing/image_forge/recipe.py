"""Receita image-forge, versão 1 (docs/GoblinShiftPlan.md, 1.2), sobre o contrato de 3.2.

Por arquivo: preparar (validar, decodificar, orientar, modo, cor) → redimensionar → gravar →
miniatura. Um arquivo recusado vira `ItemRejected` e o lote continua (1.1). A gravação fica fora
da tradução de erros do decodificador: um `ENOSPC` ou `OUTPUT_LIMIT_EXCEEDED` derruba a
execução em vez de virar rejeição.

O relatório registra fatos, não avisos (ambiguidade A2): perfil assumido ou convertido e quadros
MPO descartados ficam em `details`, sem mudar o estado para `succeeded_with_warnings`.
"""

import io
from collections.abc import Sequence
from dataclasses import dataclass

from PIL import Image

from goblinshift.processing.image_forge.color import ColorProfile
from goblinshift.processing.image_forge.encoding import SaveFormat, encode
from goblinshift.processing.image_forge.geometry import fit_size
from goblinshift.processing.image_forge.limits import MAX_INPUTS, Limits
from goblinshift.processing.image_forge.params import WHITE, OutputFormat, Params
from goblinshift.processing.image_forge.preparation import Prepared, prepare
from goblinshift.processing.image_forge.rejection import ImageRejected
from goblinshift.processing.names import safe_stem
from goblinshift.processing.previews import PendingPreview, attach_previews, write_previews
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


class ImageForgeV1:
    def __init__(self, limits: Limits | None = None, max_inputs: int = MAX_INPUTS) -> None:
        self._limits = limits or Limits()
        self._max_inputs = max_inputs

    def run(
        self, params: Params, inputs: Sequence[InputFile], out: OutputDir, progress: Progress
    ) -> Report:
        require_max_inputs(inputs, self._max_inputs, "imagens")
        items: list[ItemOk | ItemRejected] = []
        pending: list[PendingPreview] = []
        for source in inputs:
            items.append(self._forge_one(source, params, out, pending))
            progress.item_done(source.position)
        details: dict[str, DetailValue] = {
            "resampling": RESAMPLING_NAME,
            "thumbnail_box": self._limits.thumbnail_box,
        }
        return Report(attach_previews(items, write_previews(out, pending)), details=details)

    def _forge_one(
        self, source: InputFile, params: Params, out: OutputDir, pending: list[PendingPreview]
    ) -> ItemOk | ItemRejected:
        try:
            prepared = prepare(source.path, self._limits)
        except ImageRejected as rejection:
            return ItemRejected(source.position, rejection.code, rejection.reason)
        writer = _Writer(
            out,
            source.position,
            safe_stem(source.original_name, FALLBACK_STEM),
            _save_format(params.output_format, prepared.source_format),
            params,
        )
        resized = _fit(prepared.image, params.max_width, params.max_height)
        outputs = [writer.write(resized, OutputGroup.IMAGES)]
        # Guardado em memória (no máximo 512 x 512) e gravado depois de todo o conteúdo.
        pending.append(
            PendingPreview(
                source.position, outputs[0], writer.stem + PREVIEW_EXTENSION, _preview(resized)
            )
        )
        if params.make_thumbnails:
            box = self._limits.thumbnail_box
            outputs.append(writer.write(_fit(resized, box, box), OutputGroup.THUMBNAILS))
        return ItemOk(
            position=source.position,
            outputs=tuple(outputs),
            details=_details(source, prepared, resized, writer.save_format),
            notices=_notices(prepared),
        )


@dataclass(frozen=True)
class _Writer:
    """Grava as saídas de uma entrada: mesmo radical e formato em `images/` e `thumbnails/`."""

    out: OutputDir
    position: int
    stem: str
    save_format: SaveFormat
    params: Params

    def write(self, image: Image.Image, group: OutputGroup) -> str:
        output = self.out.create(
            self.stem + self.save_format.extension, OutputRole.CONTENT, self.position, group
        )
        with output.stream as stream:
            encode(
                image,
                self.save_format,
                stream,
                jpeg_quality=self.params.jpeg_quality,
                jpeg_background=self.params.jpeg_background,
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


def _save_format(requested: OutputFormat, source_format: str) -> SaveFormat:
    """`preserve`: PNG continua PNG; JPEG e MPO saem JPEG (ambiguidade A3)."""
    match requested:
        case OutputFormat.JPEG:
            return SaveFormat.JPEG
        case OutputFormat.PNG:
            return SaveFormat.PNG
        case OutputFormat.PRESERVE:
            return SaveFormat.PNG if source_format == "png" else SaveFormat.JPEG


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
