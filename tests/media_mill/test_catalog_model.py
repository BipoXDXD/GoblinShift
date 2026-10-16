"""Parâmetros do media-mill v1 como modelo Pydantic estrito do catálogo (1.5, 1.6; ADR 036)."""

import json
from typing import Any

import pytest

from goblinshift.catalog import CATALOG, InvalidParametersError, VersionStatus
from goblinshift.processing import media_mill
from goblinshift.processing.registry import RECIPES

DEFAULTS = {
    "audio_format": "wav",
    "audio_stream_index": None,
    "extract_audio": True,
    "thumbnail_count": 1,
}
MIB = 1024 * 1024


def resolve(raw: Any) -> dict[str, object]:
    return CATALOG.resolve("media-mill", 1, raw)


def error_locations(raw: Any) -> list[str]:
    with pytest.raises(InvalidParametersError) as error:
        resolve(raw)
    return [field.location for field in error.value.errors]


def test_defaults_resolvidos_viram_os_params_da_biblioteca() -> None:
    resolved = resolve({})

    assert resolved == DEFAULTS
    assert media_mill.params_from_mapping(resolved) == media_mill.Params()


def test_parametros_validos_resolvem_em_forma_canonica() -> None:
    resolved = resolve({"thumbnail_count": 3, "audio_stream_index": 2, "audio_format": "flac"})

    assert resolved == {
        "audio_format": "flac",
        "audio_stream_index": 2,
        "extract_audio": True,
        "thumbnail_count": 3,
    }
    assert list(resolved) == sorted(resolved)


@pytest.mark.parametrize(
    ("raw", "location"),
    [
        ({"thumbnail_count": 2}, "thumbnail_count"),
        ({"thumbnail_count": "1"}, "thumbnail_count"),
        ({"thumbnail_count": True}, "thumbnail_count"),
        ({"audio_stream_index": -1}, "audio_stream_index"),
        ({"audio_stream_index": 16}, "audio_stream_index"),
        ({"audio_stream_index": 1.0}, "audio_stream_index"),
        ({"audio_format": "mp3"}, "audio_format"),
        ({"extract_audio": "true"}, "extract_audio"),
        ({"inventado": 1}, "inventado"),
    ],
)
def test_tipo_ou_valor_errado_e_recusado_no_campo(raw: dict[str, Any], location: str) -> None:
    assert location in error_locations(raw)


@pytest.mark.parametrize(
    "raw",
    [
        {"extract_audio": False, "thumbnail_count": 0},
        {"extract_audio": False, "audio_format": "flac"},
        {"extract_audio": False, "audio_stream_index": 0},
    ],
)
def test_combinacao_invalida_vira_erro_na_raiz(raw: dict[str, Any]) -> None:
    assert error_locations(raw) == [""]


def test_versao_atual_publica_os_limites_de_1_5_e_1_7() -> None:
    version = CATALOG.get_version("media-mill", 1)

    assert version.status is VersionStatus.CURRENT
    assert version.limits == {
        "max_inputs": 5,
        "max_input_bytes": 256 * MIB,
        "max_total_input_bytes": 512 * MIB,
        "max_duration_s": 300,
        "max_long_side_px": 1920,
        "max_short_side_px": 1080,
        "max_fps": 60,
        "max_audio_sample_rate_hz": 48_000,
        "max_audio_channels": 2,
        "thumbnail_max_side_px": 640,
    }


def test_schema_publica_enum_das_miniaturas_e_indice_anulavel() -> None:
    parameters = CATALOG.get_version("media-mill", 1).parameters_schema()

    assert parameters["additionalProperties"] is False
    assert parameters["properties"]["thumbnail_count"]["enum"] == [0, 1, 3]
    assert parameters["properties"]["audio_format"]["enum"] == ["wav", "flac"]
    index = json.dumps(parameters["properties"]["audio_stream_index"])
    assert '"maximum": 15' in index
    assert '"null"' in index
    assert "$ref" not in json.dumps(parameters)


def test_versao_tem_implementacao_no_registro() -> None:
    assert ("media-mill", 1) in RECIPES
