"""Parâmetros do Media Mill (docs/GoblinShiftPlan.md, 1.5; ADR 036)."""

from typing import Any

import pytest

from goblinshift.processing.media_mill import (
    AudioFormat,
    InvalidParametersError,
    Params,
    params_from_mapping,
)


def test_defaults_extraem_wav_da_primeira_faixa_e_uma_miniatura() -> None:
    assert Params() == Params(
        extract_audio=True,
        audio_format=AudioFormat.WAV,
        audio_stream_index=None,
        thumbnail_count=1,
    )


@pytest.mark.parametrize("count", [0, 1, 3])
def test_quantidade_de_miniaturas_aceita_0_1_e_3(count: int) -> None:
    assert Params(thumbnail_count=count).thumbnail_count == count


@pytest.mark.parametrize("count", [-1, 2, 4])
def test_quantidade_de_miniaturas_fora_de_0_1_3_e_recusada(count: int) -> None:
    with pytest.raises(InvalidParametersError, match="thumbnail_count"):
        Params(thumbnail_count=count)


@pytest.mark.parametrize("index", [0, 15])
def test_indice_de_faixa_aceita_0_a_15(index: int) -> None:
    assert Params(audio_stream_index=index).audio_stream_index == index


@pytest.mark.parametrize("index", [-1, 16])
def test_indice_de_faixa_fora_de_0_a_15_e_recusado(index: int) -> None:
    with pytest.raises(InvalidParametersError, match="audio_stream_index"):
        Params(audio_stream_index=index)


def test_sem_audio_e_sem_miniaturas_e_recusado() -> None:
    with pytest.raises(InvalidParametersError, match="ao menos uma saída"):
        Params(extract_audio=False, thumbnail_count=0)


def test_so_miniaturas_e_aceito() -> None:
    assert not Params(extract_audio=False, thumbnail_count=3).extract_audio


@pytest.mark.parametrize(
    ("options", "field"),
    [
        pytest.param({"audio_format": AudioFormat.FLAC}, "audio_format", id="formato"),
        pytest.param({"audio_stream_index": 0}, "audio_stream_index", id="indice"),
    ],
)
def test_opcao_de_audio_sem_extrair_audio_e_recusada(options: dict[str, Any], field: str) -> None:
    with pytest.raises(InvalidParametersError, match=field):
        Params(extract_audio=False, thumbnail_count=1, **options)


def test_mapping_resolvido_vira_params() -> None:
    raw = {
        "extract_audio": True,
        "audio_format": "flac",
        "audio_stream_index": 2,
        "thumbnail_count": 3,
    }

    assert params_from_mapping(raw) == Params(
        extract_audio=True,
        audio_format=AudioFormat.FLAC,
        audio_stream_index=2,
        thumbnail_count=3,
    )


def test_mapping_vazio_usa_os_defaults() -> None:
    assert params_from_mapping({}) == Params()


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        pytest.param({"extra": 1}, "desconhecidas", id="campo-desconhecido"),
        pytest.param({"extract_audio": 1}, "extract_audio", id="booleano-como-inteiro"),
        pytest.param({"audio_format": "mp3"}, "audio_format", id="formato-fora-da-lista"),
        pytest.param({"audio_format": 1}, "audio_format", id="formato-nao-texto"),
        pytest.param({"audio_stream_index": True}, "audio_stream_index", id="indice-booleano"),
        pytest.param({"audio_stream_index": "0"}, "audio_stream_index", id="indice-texto"),
        pytest.param({"thumbnail_count": 1.0}, "thumbnail_count", id="miniaturas-float"),
        pytest.param({"thumbnail_count": False}, "thumbnail_count", id="miniaturas-booleano"),
    ],
)
def test_mapping_com_tipo_errado_e_recusado(raw: dict[str, object], message: str) -> None:
    with pytest.raises(InvalidParametersError, match=message):
        params_from_mapping(raw)
