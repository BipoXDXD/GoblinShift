"""Comparação antes/depois por item, a partir de `counts` e `details` do relatório da receita."""

from enum import StrEnum

import pytest

from goblinshift.processing.image_forge.encoding import SaveFormat
from goblinshift.processing.image_forge.inspection import ACCEPTED_FORMATS, MPO
from goblinshift.processing.media_mill import probe
from goblinshift.processing.media_mill.params import AudioFormat as MediaMillAudioFormat
from goblinshift.runs.comparison import (
    KIND_IMAGE,
    KIND_MEDIA,
    KIND_PDF,
    KIND_TABLE,
    AudioCodec,
    AudioFormat,
    ImageComparison,
    ImageFormat,
    ItemComparison,
    MediaComparison,
    PdfComparison,
    TableComparison,
    comparison_as_json,
    comparison_from_json,
    comparison_from_report,
)

IMAGE_DETAILS: dict[str, object] = {
    "width_before": 2400,
    "height_before": 1200,
    "width_after": 1600,
    "height_after": 800,
    "format_before": "mpo",
    "format_after": "jpeg",
    "bytes_before": 5_000_000,
    "color_profile": "srgb",
    "orientation_applied": 1,
    "mpo_frames_discarded": 1,
}
MEDIA_DETAILS: dict[str, object] = {
    "width": 1920,
    "height": 1080,
    "thumbnail_width": 320,
    "thumbnail_height": 180,
    "audio_codec": "aac",
    "audio_format": "wav",
    "duration_ms": 125_000,
}
EXAMPLES: list[ItemComparison] = [
    TableComparison(1, 1),
    PdfComparison(3, 0),
    ImageComparison(2, 2, 1, 1, ImageFormat.PNG, ImageFormat.PNG, 10, 7),
    MediaComparison(640, 360, None, None, AudioCodec.OPUS, AudioFormat.FLAC),
]


def without(details: dict[str, object], *keys: str) -> dict[str, object]:
    return {key: value for key, value in details.items() if key not in keys}


def values(enum: type[StrEnum]) -> set[str]:
    return {member.value for member in enum}


# --- Por receita.


def test_csv_compara_registros_lidos_e_gravados() -> None:
    counts = {
        "records_read": 120,
        "filtered_out": 3,
        "duplicates_removed": 19,
        "written": 98,
        "blank_lines_ignored": 0,
    }
    assert comparison_from_report("csv-janitor", counts, {}) == TableComparison(120, 98)


def test_pdf_compara_paginas_de_entrada_e_de_saida() -> None:
    comparison = comparison_from_report(
        "pdf-press", {"pages": 12, "output_pages": 5}, {"pdf_version": "1.7"}
    )
    assert comparison == PdfComparison(12, 5)


def test_imagem_compara_dimensoes_formato_e_tamanho_de_entrada() -> None:
    assert comparison_from_report("image-forge", {}, IMAGE_DETAILS) == ImageComparison(
        2400, 1200, 1600, 800, ImageFormat.MPO, ImageFormat.JPEG, 5_000_000, None
    )


def test_video_com_miniaturas_e_audio_compara_dimensoes_e_audio() -> None:
    assert comparison_from_report("media-mill", {}, MEDIA_DETAILS) == MediaComparison(
        1920, 1080, 320, 180, AudioCodec.AAC, AudioFormat.WAV
    )


@pytest.mark.parametrize(
    ("dropped", "expected"),
    [
        (
            ("thumbnail_width", "thumbnail_height"),
            MediaComparison(1920, 1080, None, None, AudioCodec.AAC, AudioFormat.WAV),
        ),
        (("audio_codec", "audio_format"), MediaComparison(1920, 1080, 320, 180, None, None)),
    ],
)
def test_video_sem_miniaturas_nem_audio_compara_so_o_que_existe(
    dropped: tuple[str, str], expected: MediaComparison
) -> None:
    details = without(MEDIA_DETAILS, *dropped)
    assert comparison_from_report("media-mill", {}, details) == expected


def test_video_sem_miniaturas_nem_audio_nao_tem_comparacao() -> None:
    details = without(
        MEDIA_DETAILS, "thumbnail_width", "thumbnail_height", "audio_codec", "audio_format"
    )
    assert comparison_from_report("media-mill", {}, details) is None


@pytest.mark.parametrize("recipe_id", ["downloads-detox", "receita-nova"])
def test_detox_e_receita_desconhecida_nao_tem_comparacao(recipe_id: str) -> None:
    counts = {"records_read": 1, "written": 1, "pages": 1, "output_pages": 1}
    assert comparison_from_report(recipe_id, counts, IMAGE_DETAILS | MEDIA_DETAILS) is None


# --- O executor é não confiável: o que foge do tipo ou da lista vira ausência.


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("width_after", True),
        ("width_after", -1),
        ("width_after", 0),
        ("width_after", "800"),
        ("width_after", None),
        ("format_before", "a.jpg"),
        ("format_after", "gif"),
        ("format_after", 1),
        ("bytes_before", 1.5),
        ("bytes_before", -1),
    ],
)
def test_chave_fora_do_tipo_ou_da_lista_vira_ausencia(key: str, value: object) -> None:
    assert comparison_from_report("image-forge", {}, IMAGE_DETAILS | {key: value}) is None


@pytest.mark.parametrize(
    "details",
    [
        MEDIA_DETAILS | {"audio_codec": "<b>"},
        MEDIA_DETAILS | {"audio_format": "mp3"},
        MEDIA_DETAILS | {"thumbnail_height": 0},
        without(MEDIA_DETAILS, "thumbnail_height"),
        without(MEDIA_DETAILS, "audio_format"),
    ],
)
def test_video_com_par_pela_metade_ou_fora_da_lista_vira_ausencia(
    details: dict[str, object],
) -> None:
    assert comparison_from_report("media-mill", {}, details) is None


def test_chave_obrigatoria_ausente_vira_ausencia() -> None:
    assert comparison_from_report("image-forge", {}, without(IMAGE_DETAILS, "width_after")) is None


def test_contagem_ausente_vira_ausencia() -> None:
    assert comparison_from_report("pdf-press", {"pages": 3}, {}) is None


# --- Ida e volta pelo JSON guardado no banco.


@pytest.mark.parametrize("comparison", EXAMPLES)
def test_ida_e_volta_pelo_json_do_banco(comparison: ItemComparison) -> None:
    assert comparison_from_json(comparison_as_json(comparison)) == comparison


def test_json_traz_o_kind_e_os_valores_dos_enums() -> None:
    assert comparison_as_json(EXAMPLES[2]) == {
        "kind": "image",
        "width_before": 2,
        "height_before": 2,
        "width_after": 1,
        "height_after": 1,
        "format_before": "png",
        "format_after": "png",
        "bytes_before": 10,
        "bytes_after": 7,
    }


@pytest.mark.parametrize("comparison", EXAMPLES)
def test_json_so_tem_inteiros_e_valores_das_listas_fechadas(comparison: ItemComparison) -> None:
    closed = values(ImageFormat) | values(AudioCodec) | values(AudioFormat)
    raw = comparison_as_json(comparison)

    assert raw.pop("kind") in {KIND_TABLE, KIND_PDF, KIND_IMAGE, KIND_MEDIA}
    for value in raw.values():
        is_int = isinstance(value, int) and not isinstance(value, bool)
        assert value is None or is_int or (type(value) is str and value in closed), value


@pytest.mark.parametrize(
    "raw",
    [
        None,
        [],
        "table",
        {"kind": "zip"},
        {"kind": ["table"]},
        {"kind": "table", "records_before": 1},
        {"kind": "table", "records_before": 1, "records_after": 1, "extra": 1},
        {"records_before": 1, "records_after": 1},
        {"kind": "pdf", "pages_before": 1, "pages_after": False},
        {
            "kind": "media",
            "width_before": 640,
            "height_before": 360,
            "width_after": None,
            "height_after": None,
            "audio_before": None,
            "audio_after": None,
        },
    ],
)
def test_json_fora_da_forma_vira_ausencia(raw: object) -> None:
    assert comparison_from_json(raw) is None


# --- As listas fechadas acompanham as receitas.


def test_codecs_de_audio_sao_os_da_allowlist_do_media_mill() -> None:
    assert values(AudioCodec) == set().union(*probe._AUDIO_CODECS.values())


def test_formatos_de_imagem_cobrem_entrada_e_saida_da_v1() -> None:
    v1 = {name.lower() for name in (*ACCEPTED_FORMATS, MPO)} | values(SaveFormat)
    assert v1 <= values(ImageFormat)


def test_formatos_de_audio_cobrem_os_da_v1_do_media_mill() -> None:
    assert values(MediaMillAudioFormat) <= values(AudioFormat)
