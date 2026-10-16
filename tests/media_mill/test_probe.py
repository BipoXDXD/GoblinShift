"""Leitura do ffprobe e allowlist de contêiner, codec e limites (1.5; ADR 036).

Funções puras sobre o JSON do ffprobe: os casos de borda não precisam de vídeo real. As fixtures
geradas com o FFmpeg (test_recipe.py) provam que o JSON de verdade tem esta forma.
"""

import json
from typing import Any

import pytest

from goblinshift.processing.media_mill import Limits, Params
from goblinshift.processing.media_mill.probe import inspect, parse_probe
from goblinshift.processing.media_mill.rejection import MediaRejected, RejectionCode
from goblinshift.processing.media_mill.signature import Container

MOV_FORMAT = "mov,mp4,m4a,3gp,3g2,mj2"
WEBM_FORMAT = "matroska,webm"


def video(**overrides: Any) -> dict[str, Any]:
    stream = {
        "index": 0,
        "codec_type": "video",
        "codec_name": "h264",
        "width": 1920,
        "height": 1080,
        "sample_aspect_ratio": "1:1",
        "avg_frame_rate": "60/1",
        "r_frame_rate": "60/1",
        "duration": "300.000000",
    }
    return {**stream, **overrides}


def audio(index: int = 1, **overrides: Any) -> dict[str, Any]:
    stream = {
        "index": index,
        "codec_type": "audio",
        "codec_name": "aac",
        "sample_rate": "48000",
        "channels": 2,
        "avg_frame_rate": "0/0",
        "r_frame_rate": "0/0",
    }
    return {**stream, **overrides}


def probe_json(
    streams: list[dict[str, Any]], *, format_name: str = MOV_FORMAT, duration: str = "300.0"
) -> bytes:
    document = {"streams": streams, "format": {"format_name": format_name, "duration": duration}}
    return json.dumps(document).encode()


def inspected(
    data: bytes,
    container: Container = Container.MOV,
    params: Params | None = None,
) -> Any:
    return inspect(parse_probe(data), container, params or Params(), Limits())


def rejection(
    data: bytes, container: Container = Container.MOV, params: Params | None = None
) -> RejectionCode:
    with pytest.raises(MediaRejected) as rejected:
        inspected(data, container, params)
    return rejected.value.code


def test_video_no_limite_de_1_5_e_aceito_com_a_primeira_faixa_de_audio() -> None:
    media = inspected(probe_json([video(), audio()]))

    assert (media.width, media.height, media.duration_s) == (1920, 1080, 300.0)
    assert media.audio is not None
    assert (media.audio.order, media.audio.sample_rate, media.audio.channels) == (0, 48000, 2)


def test_webm_vp9_opus_e_aceito() -> None:
    data = probe_json(
        [video(codec_name="vp9", duration=None), audio(codec_name="opus")],
        format_name=WEBM_FORMAT,
    )

    assert inspected(data, Container.WEBM).duration_s == 300.0


@pytest.mark.parametrize(
    ("container", "video_codec", "audio_codec"),
    [
        pytest.param(Container.MOV, "h264", "pcm_s16le", id="mov-pcm16le"),
        pytest.param(Container.MOV, "h264", "pcm_s24be", id="mov-pcm24be"),
        pytest.param(Container.WEBM, "vp8", "vorbis", id="webm-vp8-vorbis"),
    ],
)
def test_combinacoes_da_allowlist_sao_aceitas(
    container: Container, video_codec: str, audio_codec: str
) -> None:
    format_name = MOV_FORMAT if container is Container.MOV else WEBM_FORMAT
    data = probe_json(
        [video(codec_name=video_codec), audio(codec_name=audio_codec)], format_name=format_name
    )

    assert inspected(data, container).audio is not None


@pytest.mark.parametrize(
    ("streams", "container", "format_name"),
    [
        pytest.param([video(codec_name="hevc")], Container.MOV, MOV_FORMAT, id="hevc"),
        pytest.param([video(codec_name="mpeg4")], Container.MOV, MOV_FORMAT, id="mpeg4"),
        pytest.param([video(codec_name="vp9")], Container.MOV, MOV_FORMAT, id="vp9-em-mp4"),
        pytest.param([video(codec_name="h264")], Container.WEBM, WEBM_FORMAT, id="h264-em-webm"),
        pytest.param([video(), audio(codec_name="mp3")], Container.MOV, MOV_FORMAT, id="mp3"),
        pytest.param([video(), audio(codec_name="opus")], Container.MOV, MOV_FORMAT, id="opus-mp4"),
        pytest.param(
            [video(codec_name="vp9"), audio(codec_name="aac")],
            Container.WEBM,
            WEBM_FORMAT,
            id="aac-em-webm",
        ),
        pytest.param(
            [video(), video(index=1, codec_name="mjpeg")],
            Container.MOV,
            MOV_FORMAT,
            id="capa-mjpeg",
        ),
        pytest.param([audio(index=0)], Container.MOV, MOV_FORMAT, id="sem-video"),
        pytest.param([video()], Container.MOV, "hls", id="demuxer-inesperado"),
    ],
)
def test_fora_da_allowlist_e_recusado(
    streams: list[dict[str, Any]], container: Container, format_name: str
) -> None:
    data = probe_json(streams, format_name=format_name)

    assert rejection(data, container) is RejectionCode.UNSUPPORTED_FORMAT


def test_faixas_de_dados_e_legendas_sao_ignoradas() -> None:
    data = probe_json(
        [video(), audio(), {"index": 2, "codec_type": "data", "codec_name": "bin_data"}]
    )

    assert inspected(data).audio is not None


def test_muitas_faixas_sao_recusadas() -> None:
    streams = [video(), *(audio(index=i) for i in range(1, 17))]

    assert rejection(probe_json(streams)) is RejectionCode.UNSUPPORTED_FORMAT


@pytest.mark.parametrize(
    ("overrides", "duration"),
    [
        pytest.param({"width": 1921}, "300", id="largura-1921"),
        pytest.param({"height": 1081}, "300", id="altura-1081"),
        pytest.param({"width": 3840, "height": 2160}, "300", id="4k"),
        pytest.param({"width": 1080, "height": 1921}, "300", id="retrato-1921"),
        pytest.param({"avg_frame_rate": "120/1", "r_frame_rate": "120/1"}, "300", id="120fps"),
        pytest.param({"avg_frame_rate": "6001/100"}, "300", id="60.01fps"),
        pytest.param({}, "300.001", id="duracao-300.001"),
    ],
)
def test_acima_dos_limites_e_media_too_large(overrides: dict[str, Any], duration: str) -> None:
    data = probe_json([video(**overrides), audio()], duration=duration)

    assert rejection(data) is RejectionCode.MEDIA_TOO_LARGE


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"width": 1080, "height": 1920}, id="retrato-1080x1920"),
        pytest.param({"avg_frame_rate": "60000/1001"}, id="59.94fps"),
        pytest.param({"avg_frame_rate": "0/0", "r_frame_rate": "30/1"}, id="sem-media-usa-r"),
    ],
)
def test_no_limite_e_aceito(overrides: dict[str, Any]) -> None:
    assert inspected(probe_json([video(**overrides), audio()])).width > 0


def test_duracao_do_video_acima_do_limite_tambem_recusa() -> None:
    data = probe_json([video(duration="301.0")], duration="10.0")

    assert rejection(data) is RejectionCode.MEDIA_TOO_LARGE


@pytest.mark.parametrize(
    ("overrides", "duration"),
    [
        pytest.param({"sample_aspect_ratio": "4:3"}, "10", id="pixel-nao-quadrado"),
        pytest.param({"width": 0}, "10", id="largura-zero"),
        pytest.param({"avg_frame_rate": "0/0", "r_frame_rate": "0/0"}, "10", id="sem-fps"),
        pytest.param({}, "0", id="duracao-zero"),
        pytest.param({"duration": None}, "N/A", id="duracao-ausente"),
        pytest.param({"side_data_list": [{"rotation": 45}]}, "10", id="rotacao-45"),
    ],
)
def test_perfil_fora_de_1_5_e_formato_nao_suportado(
    overrides: dict[str, Any], duration: str
) -> None:
    data = probe_json([video(**overrides)], duration=duration)

    assert rejection(data) is RejectionCode.UNSUPPORTED_FORMAT


@pytest.mark.parametrize("sar", ["1:1", "0:1", None])
def test_pixel_quadrado_ou_sem_sar_e_aceito(sar: str | None) -> None:
    assert inspected(probe_json([video(sample_aspect_ratio=sar), audio()])).width == 1920


@pytest.mark.parametrize(
    ("rotation", "size"),
    [
        (0, (1920, 1080)),
        (90, (1080, 1920)),
        (-90, (1080, 1920)),
        (180, (1920, 1080)),
        (-270, (1080, 1920)),
    ],
)
def test_rotacao_em_multiplos_de_90_vira_dimensoes_de_exibicao(
    rotation: int, size: tuple[int, int]
) -> None:
    data = probe_json(
        [
            video(side_data_list=[{"side_data_type": "Display Matrix", "rotation": rotation}]),
            audio(),
        ]
    )

    media = inspected(data)

    assert (media.display_width, media.display_height) == size
    assert media.rotation == rotation % 360


def test_faixa_indicada_conta_so_as_faixas_de_audio() -> None:
    data = probe_json(
        [video(), audio(index=1, channels=1), audio(index=2, channels=2, sample_rate="44100")]
    )

    media = inspected(data, params=Params(audio_stream_index=1))

    assert media.audio is not None
    assert (media.audio.order, media.audio.channels, media.audio.sample_rate) == (1, 2, 44100)


def test_faixa_indicada_inexistente_e_recusada() -> None:
    data = probe_json([video(), audio()])

    code = rejection(data, params=Params(audio_stream_index=1))

    assert code is RejectionCode.AUDIO_STREAM_NOT_FOUND


def test_video_sem_audio_com_extracao_pedida_e_recusado() -> None:
    assert rejection(probe_json([video()])) is RejectionCode.AUDIO_STREAM_NOT_FOUND


def test_video_sem_audio_so_com_miniaturas_e_aceito() -> None:
    media = inspected(probe_json([video()]), params=Params(extract_audio=False))

    assert media.audio is None


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"sample_rate": "48001"}, id="48001hz"),
        pytest.param({"sample_rate": "96000"}, id="96khz"),
        pytest.param({"channels": 3}, id="3-canais"),
        pytest.param({"channels": 6}, id="5.1"),
    ],
)
def test_audio_acima_de_48khz_ou_estereo_e_recusado(overrides: dict[str, Any]) -> None:
    data = probe_json([video(), audio(**overrides)])

    assert rejection(data) is RejectionCode.AUDIO_LIMIT_EXCEEDED


def test_audio_acima_do_limite_nao_importa_sem_extracao() -> None:
    data = probe_json([video(), audio(channels=6)])

    assert inspected(data, params=Params(extract_audio=False)).audio is None


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"sample_rate": "0"}, id="taxa-zero"),
        pytest.param({"channels": 0}, id="sem-canais"),
        pytest.param({"sample_rate": "abc"}, id="taxa-invalida"),
    ],
)
def test_audio_sem_taxa_ou_canais_e_formato_nao_suportado(overrides: dict[str, Any]) -> None:
    data = probe_json([video(), audio(**overrides)])

    assert rejection(data) is RejectionCode.UNSUPPORTED_FORMAT


@pytest.mark.parametrize(
    "data",
    [
        pytest.param(b"", id="vazio"),
        pytest.param(b"{", id="json-quebrado"),
        pytest.param(b"[]", id="nao-objeto"),
        pytest.param(b'{"streams": {}, "format": {}}', id="streams-nao-lista"),
        pytest.param(b'{"streams": [1], "format": {}}', id="stream-nao-objeto"),
        pytest.param(b'{"streams": []}', id="sem-format"),
        pytest.param(
            b'{"streams": [{"codec_type": "video", "width": "x"}], "format": {}}',
            id="largura-texto",
        ),
    ],
)
def test_saida_do_ffprobe_fora_do_esperado_e_formato_nao_suportado(data: bytes) -> None:
    with pytest.raises(MediaRejected) as rejected:
        parse_probe(data)

    assert rejected.value.code is RejectionCode.UNSUPPORTED_FORMAT
