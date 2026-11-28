"""Parâmetros do media-mill v2 no catálogo: superconjunto da v1, bitrate por formato, sem FFmpeg."""

import subprocess
import sys
from dataclasses import replace
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from goblinshift.catalog import CATALOG, InvalidParametersError
from goblinshift.catalog.media_mill_v2 import MediaMillV2Parameters
from goblinshift.processing import media_mill
from goblinshift.processing.media_mill import AudioFormatV2, ParamsV2
from goblinshift.processing.media_mill.plan import plan_from_v1, plan_from_v2
from goblinshift.processing.registry import RECIPES

DEFAULTS = {
    "audio_bitrate_kbps": 192,
    "audio_format": "wav",
    "audio_stream_index": None,
    "extract_audio": True,
    "thumbnail_count": 1,
    "trim_end_ms": None,
    "trim_start_ms": 0,
}


@st.composite
def v1_params(draw: st.DrawFn) -> dict[str, object]:
    """Parâmetros válidos da v1, com campos omitidos ao acaso (os defaults entram no catálogo)."""
    extract_audio = draw(st.booleans())
    raw: dict[str, object] = {
        "extract_audio": extract_audio,
        "thumbnail_count": draw(st.sampled_from([1, 3] if not extract_audio else [0, 1, 3])),
    }
    if extract_audio:
        raw["audio_format"] = draw(st.sampled_from(["wav", "flac"]))
        raw["audio_stream_index"] = draw(st.none() | st.integers(min_value=0, max_value=15))
    keep = draw(st.sets(st.sampled_from(sorted(raw))))
    kept = {name: value for name, value in raw.items() if name in keep}
    if not extract_audio:
        kept.setdefault("extract_audio", False)
        kept.setdefault("thumbnail_count", raw["thumbnail_count"])
    return kept


@given(v1_params())
def test_parametros_da_v1_valem_na_v2_com_o_mesmo_plano(raw: dict[str, object]) -> None:
    v1 = CATALOG.resolve("media-mill", 1, raw)
    v2 = CATALOG.resolve("media-mill", 2, v1)
    expected = replace(plan_from_v1(media_mill.params_from_mapping(v1)), v2_details=True)
    assert plan_from_v2(media_mill.params_v2_from_mapping(v2)) == expected


def test_defaults_resolvidos_viram_os_params_v2_da_biblioteca() -> None:
    resolved = CATALOG.resolve("media-mill", 2, {})

    assert resolved == DEFAULTS
    assert media_mill.params_v2_from_mapping(resolved) == ParamsV2()


def test_converte_para_params_v2_da_biblioteca() -> None:
    model = MediaMillV2Parameters.model_validate(
        {"audio_format": "aac", "audio_bitrate_kbps": 96, "trim_start_ms": 500, "trim_end_ms": 900}
    )

    assert model.to_params() == ParamsV2(
        audio_format=AudioFormatV2.AAC, audio_bitrate_kbps=96, trim_start_ms=500, trim_end_ms=900
    )


@pytest.mark.parametrize(
    ("raw", "location"),
    [
        ({"audio_format": "ogg"}, "audio_format"),
        ({"audio_bitrate_kbps": 100}, "audio_bitrate_kbps"),
        ({"audio_bitrate_kbps": True}, "audio_bitrate_kbps"),
        ({"audio_bitrate_kbps": "192"}, "audio_bitrate_kbps"),
        ({"trim_start_ms": -1}, "trim_start_ms"),
        ({"trim_start_ms": 300_001}, "trim_start_ms"),
        ({"trim_end_ms": 0}, "trim_end_ms"),
        ({"trim_end_ms": 1.5}, "trim_end_ms"),
        ({"thumbnail_count": True}, "thumbnail_count"),
        ({"trim": 10}, "trim"),
    ],
)
def test_tipo_ou_valor_errado_e_recusado_no_campo(raw: dict[str, Any], location: str) -> None:
    with pytest.raises(InvalidParametersError) as error:
        CATALOG.resolve("media-mill", 2, raw)
    assert location in [".".join(field.location) for field in error.value.errors]


@pytest.mark.parametrize(
    "raw",
    [
        {"audio_format": "mp3", "audio_bitrate_kbps": 96},
        {"audio_format": "opus", "audio_bitrate_kbps": 256},
        {"audio_format": "flac", "audio_bitrate_kbps": 128},
        {"trim_start_ms": 2000, "trim_end_ms": 2000},
        {"extract_audio": False, "audio_bitrate_kbps": 128},
    ],
)
def test_combinacao_invalida_vira_erro_na_raiz(raw: dict[str, Any]) -> None:
    with pytest.raises(InvalidParametersError) as error:
        CATALOG.resolve("media-mill", 2, raw)
    assert [".".join(field.location) for field in error.value.errors] == [""]


def test_schema_publica_o_bitrate_de_cada_formato() -> None:
    schema = CATALOG.get_version("media-mill", 2).parameters_schema()

    by_format = {
        rule["if"]["properties"]["audio_format"]["const"]: rule["then"]["properties"][
            "audio_bitrate_kbps"
        ]["enum"]
        for rule in schema["allOf"]
    }
    assert by_format == {
        "mp3": [128, 160, 192, 256, 320],
        "aac": [96, 128, 160, 192, 256],
        "opus": [64, 96, 128, 160, 192],
    }
    properties = schema["properties"]
    assert properties["audio_format"]["enum"] == ["wav", "flac", "mp3", "aac", "opus"]
    assert properties["audio_bitrate_kbps"]["enum"] == [64, 96, 128, 160, 192, 256, 320]
    assert (properties["trim_start_ms"]["minimum"], properties["trim_start_ms"]["maximum"]) == (
        0,
        300_000,
    )
    assert schema["additionalProperties"] is False


def test_versao_2_tem_implementacao_no_registro() -> None:
    assert ("media-mill", 2) in RECIPES


def test_importar_o_modelo_v2_nao_chama_o_ffmpeg() -> None:
    code = (
        "import sys, goblinshift.catalog.media_mill_v2;"
        "runs_ffmpeg = ('goblinshift.processing.media_mill.recipe',"
        " 'goblinshift.processing.media_mill.commands');"
        "sys.exit(1 if any(m in sys.modules for m in runs_ffmpeg) else 0)"
    )
    # Processo novo para ver o `sys.modules` limpo; o código é a constante acima, sem entrada.
    result = subprocess.run([sys.executable, "-c", code], check=False)  # noqa: S603 - código fixo
    assert result.returncode == 0


def test_default_do_bitrate_no_modelo_e_o_da_biblioteca() -> None:
    assert MediaMillV2Parameters().audio_bitrate_kbps == media_mill.DEFAULT_BITRATE_KBPS
