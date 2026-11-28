"""`MillPlan` e os parâmetros da v2: os dois adapters montam o plano do mesmo pipeline."""

from collections.abc import Mapping

import pytest

from goblinshift.processing import media_mill
from goblinshift.processing.media_mill import (
    AudioFormat,
    AudioFormatV2,
    InvalidParametersError,
    Params,
    ParamsV2,
    params_v2_from_mapping,
)
from goblinshift.processing.media_mill.plan import MillPlan, plan_from_v1, plan_from_v2


def test_plano_da_v1_nao_corta_nem_comprime() -> None:
    assert plan_from_v1(Params()) == MillPlan(True, AudioFormatV2.WAV, 192, None, 1, 0, None)


def test_plano_da_v1_leva_os_parametros_dela() -> None:
    params = Params(audio_format=AudioFormat.FLAC, audio_stream_index=2, thumbnail_count=3)

    assert plan_from_v1(params) == MillPlan(True, AudioFormatV2.FLAC, 192, 2, 3, 0, None)


def test_plano_da_v2_leva_bitrate_e_corte() -> None:
    params = params_v2_from_mapping(
        {
            "audio_format": "opus",
            "audio_bitrate_kbps": 96,
            "thumbnail_count": 3,
            "trim_start_ms": 500,
            "trim_end_ms": 1500,
        }
    )

    plan = plan_from_v2(params)

    assert plan == MillPlan(True, AudioFormatV2.OPUS, 96, None, 3, 500, 1500, v2_details=True)


def test_defaults_da_v2_sao_os_da_v1_mais_bitrate_e_sem_corte() -> None:
    assert params_v2_from_mapping({}) == ParamsV2(
        extract_audio=True,
        audio_format=AudioFormatV2.WAV,
        audio_stream_index=None,
        thumbnail_count=1,
        audio_bitrate_kbps=192,
        trim_start_ms=0,
        trim_end_ms=None,
    )


@pytest.mark.parametrize(
    ("raw", "field"),
    [
        ({"audio_format": "aac", "audio_bitrate_kbps": 320}, "audio_bitrate_kbps"),
        ({"audio_format": "opus", "audio_bitrate_kbps": 256}, "audio_bitrate_kbps"),
        ({"audio_format": "mp3", "audio_bitrate_kbps": 96}, "audio_bitrate_kbps"),
        ({"audio_bitrate_kbps": 128}, "audio_bitrate_kbps"),
        ({"audio_format": "flac", "audio_bitrate_kbps": 256}, "audio_bitrate_kbps"),
        ({"extract_audio": False, "audio_bitrate_kbps": 128}, "audio_bitrate_kbps"),
        ({"extract_audio": False, "audio_format": "mp3"}, "audio_format"),
        ({"trim_start_ms": 5000, "trim_end_ms": 5000}, "trim_end_ms"),
        ({"trim_start_ms": 5000, "trim_end_ms": 4999}, "trim_end_ms"),
        ({"trim_start_ms": -1}, "trim_start_ms"),
        ({"trim_start_ms": 300_001}, "trim_start_ms"),
        ({"trim_end_ms": 0}, "trim_end_ms"),
        ({"trim_end_ms": 300_001}, "trim_end_ms"),
        ({"trim_start_ms": 1.5}, "trim_start_ms"),
        ({"trim_end_ms": True}, "trim_end_ms"),
        ({"audio_bitrate_kbps": "192"}, "audio_bitrate_kbps"),
        ({"audio_format": "ogg"}, "audio_format"),
        ({"extract_audio": False, "thumbnail_count": 0}, "extract_audio"),
        ({"trim": 10}, "trim"),
    ],
)
def test_combinacao_invalida_da_v2_e_recusada(raw: Mapping[str, object], field: str) -> None:
    with pytest.raises(InvalidParametersError, match=field):
        params_v2_from_mapping(raw)


@pytest.mark.parametrize(
    "raw",
    [
        {"audio_format": "mp3", "audio_bitrate_kbps": 320},
        {"audio_format": "aac", "audio_bitrate_kbps": 96},
        {"audio_format": "opus", "audio_bitrate_kbps": 64},
        {"audio_format": "flac"},
        {"trim_start_ms": 300_000},
        {"trim_end_ms": 1},
        {"trim_start_ms": 0, "trim_end_ms": 300_000},
        {"extract_audio": False, "thumbnail_count": 3, "trim_start_ms": 1000},
    ],
)
def test_combinacao_valida_da_v2_e_aceita(raw: Mapping[str, object]) -> None:
    params_v2_from_mapping(raw)


def test_version_continua_apontando_para_a_v1() -> None:
    assert (media_mill.VERSION, media_mill.VERSION_1, media_mill.VERSION_2) == (1, 1, 2)
