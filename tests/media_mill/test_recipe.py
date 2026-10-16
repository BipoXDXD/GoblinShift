"""Receita media-mill de ponta a ponta com o FFmpeg real (1.5, 2.3; ADR 036).

Os vídeos são gerados na hora (tests/media_mill/media.py), inclusive os hostis do plano: HLS
disfarçado de .mp4, lista do concat, codec fora da allowlist, 4K, 120 fps, 6 canais, mais de 300 s
e arquivo truncado.
"""

import itertools
from collections.abc import Iterator
from pathlib import Path

import pytest
from PIL import Image

from goblinshift.processing.media_mill import AudioFormat, Params
from goblinshift.processing.media_mill.recipe import MediaMillV1
from goblinshift.processing.media_mill.tools import Tools
from goblinshift.processing.recipe import (
    FailureCode,
    InputContractError,
    ItemOk,
    ItemRejected,
    RecipeFailure,
)
from tests.media_mill.conftest import ClipLibrary, Workspace
from tests.media_mill.media import (
    CANARY,
    FFCONCAT,
    HLS_PLAYLIST,
    corrupted,
    probe,
    rotated,
    truncated,
)


def ok_item(workspace: Workspace, params: Params | None = None) -> ItemOk:
    report = workspace.run(params)
    item = report.items[0]
    assert isinstance(item, ItemOk), item
    return item


def rejected_item(workspace: Workspace, params: Params | None = None) -> ItemRejected:
    report = workspace.run(params)
    item = report.items[0]
    assert isinstance(item, ItemRejected), item
    return item


def test_mp4_vira_wav_16_bits_na_taxa_e_canais_de_origem_e_uma_miniatura(
    workspace: Workspace, clips: ClipLibrary
) -> None:
    workspace.add("férias.mp4", clips.bytes("mp4"))

    item = ok_item(workspace)

    assert item.outputs == ("audio/férias.wav", "thumbnails/férias-10pct.jpg")
    assert workspace.files() == ["audio/férias.wav", "thumbnails/férias-10pct.jpg"]
    wav = probe(workspace.path("audio/férias.wav"))
    assert (wav["codec_name"], wav["sample_rate"], wav["channels"]) == ("pcm_s16le", "44100", "2")


def test_flac_sai_com_amostras_de_16_bits(workspace: Workspace, clips: ClipLibrary) -> None:
    workspace.add("clip.mov", clips.bytes("mov_pcm24"))

    item = ok_item(workspace, Params(audio_format=AudioFormat.FLAC, thumbnail_count=0))

    assert item.outputs == ("audio/clip.flac",)
    flac = probe(workspace.path("audio/clip.flac"))
    assert (flac["codec_name"], flac["sample_fmt"], flac["sample_rate"]) == ("flac", "s16", "48000")


def test_pcm_de_24_bits_vira_wav_de_16_bits(workspace: Workspace, clips: ClipLibrary) -> None:
    workspace.add("clip.mov", clips.bytes("mov_pcm24"))

    ok_item(workspace, Params(thumbnail_count=0))

    wav = probe(workspace.path("audio/clip.wav"))
    assert (wav["codec_name"], wav["sample_rate"], wav["channels"]) == ("pcm_s16le", "48000", "2")


def test_mono_continua_mono_sem_reamostragem(workspace: Workspace, clips: ClipLibrary) -> None:
    workspace.add("clip.mov", clips.bytes("mov_pcm16_mono"))

    item = ok_item(workspace, Params(thumbnail_count=0))

    wav = probe(workspace.path("audio/clip.wav"))
    assert (wav["sample_rate"], wav["channels"]) == ("32000", "1")
    assert item.details["audio_channels"] == 1


@pytest.mark.parametrize(
    ("clip", "codecs", "rate"),
    [
        pytest.param("webm_vp9_opus", ("vp9", "opus"), "48000", id="vp9-opus"),
        pytest.param("webm_vp8_vorbis", ("vp8", "vorbis"), "44100", id="vp8-vorbis"),
    ],
)
def test_webm_da_allowlist_e_convertido(
    workspace: Workspace, clips: ClipLibrary, clip: str, codecs: tuple[str, str], rate: str
) -> None:
    workspace.add("clip.webm", clips.bytes(clip))

    item = ok_item(workspace)

    assert (item.details["video_codec"], item.details["audio_codec"]) == codecs
    assert item.details["container"] == "webm"
    assert probe(workspace.path("audio/clip.wav"))["sample_rate"] == rate


def test_tres_miniaturas_em_10_50_e_90_por_cento(workspace: Workspace, clips: ClipLibrary) -> None:
    workspace.add("clip.mp4", clips.bytes("mp4"))

    item = ok_item(workspace, Params(extract_audio=False, thumbnail_count=3))

    assert item.outputs == (
        "thumbnails/clip-10pct.jpg",
        "thumbnails/clip-50pct.jpg",
        "thumbnails/clip-90pct.jpg",
    )
    # 2 s a 30 fps: os alvos 0,2 s, 1,0 s e 1,8 s caem exatamente em quadros.
    requested = [item.details[f"thumbnail_{p}_requested_ms"] for p in ("10", "50", "90")]
    effective = [item.details[f"thumbnail_{p}_effective_ms"] for p in ("10", "50", "90")]
    assert requested == [200, 1000, 1800]
    assert effective == [200, 1000, 1800]
    assert item.warnings == ()


def test_miniatura_sem_quadro_depois_do_alvo_usa_o_ultimo_anterior_com_aviso(
    workspace: Workspace, clips: ClipLibrary
) -> None:
    workspace.add("curto.webm", clips.bytes("webm_short_video"))

    item = ok_item(workspace, Params(extract_audio=False, thumbnail_count=3))

    # Vídeo de 1 s num WebM de 3 s: os alvos de 50% (1,5 s) e 90% (2,7 s) passam do último quadro.
    assert len(item.outputs) == 3
    assert [w.code for w in item.warnings] == ["THUMBNAIL_BEFORE_TARGET"] * 2
    # 10%: o primeiro quadro em ou depois de ~0,301 s é o de 10/30 s; 50% e 90% ficam no último
    # quadro, 29/30 s. A duração do WebM vem do Opus (~3,008 s), então o alvo varia por encoder.
    effective = [item.details[f"thumbnail_{p}_effective_ms"] for p in ("10", "50", "90")]
    assert effective == [333, 967, 967]
    assert item.details["thumbnail_90_requested_ms"] in range(2700, 2711)


def test_miniatura_cabe_em_640_sem_distorcer(workspace: Workspace, clips: ClipLibrary) -> None:
    workspace.add("hd.mp4", clips.bytes("mp4_full_hd"))

    item = ok_item(workspace, Params(extract_audio=False))

    with Image.open(workspace.path("thumbnails/hd-10pct.jpg")) as thumbnail:
        assert (thumbnail.format, thumbnail.size) == ("JPEG", (640, 360))
    assert (item.details["thumbnail_width"], item.details["thumbnail_height"]) == (640, 360)


def test_miniatura_pequena_nao_e_ampliada(workspace: Workspace, clips: ClipLibrary) -> None:
    workspace.add("clip.mp4", clips.bytes("mp4"))

    ok_item(workspace, Params(extract_audio=False))

    with Image.open(workspace.path("thumbnails/clip-10pct.jpg")) as thumbnail:
        assert thumbnail.size == (320, 240)


def test_video_girado_90_graus_gera_miniatura_em_pe(
    workspace: Workspace, clips: ClipLibrary, tmp_path: Path
) -> None:
    source = rotated(clips.path("mp4"), tmp_path / "rotated.mp4", 90)
    workspace.add("celular.mp4", source.read_bytes())

    item = ok_item(workspace, Params(extract_audio=False))

    with Image.open(workspace.path("thumbnails/celular-10pct.jpg")) as thumbnail:
        assert thumbnail.size == (240, 320)
    assert (item.details["width"], item.details["height"], item.details["rotation"]) == (
        240,
        320,
        90,
    )


def test_sem_indice_usa_a_primeira_faixa_e_registra_o_indice_efetivo(
    workspace: Workspace, clips: ClipLibrary
) -> None:
    workspace.add("duas.mp4", clips.bytes("mp4_mono_and_stereo"))

    item = ok_item(workspace, Params(thumbnail_count=0))

    assert item.details["audio_stream_index"] == 0
    assert probe(workspace.path("audio/duas.wav"))["channels"] == "1"


def test_indice_escolhe_a_faixa_entre_as_de_audio(workspace: Workspace, clips: ClipLibrary) -> None:
    workspace.add("duas.mp4", clips.bytes("mp4_mono_and_stereo"))

    item = ok_item(workspace, Params(audio_stream_index=1, thumbnail_count=0))

    assert item.details["audio_stream_index"] == 1
    wav = probe(workspace.path("audio/duas.wav"))
    assert (wav["channels"], wav["sample_rate"]) == ("2", "48000")


def test_video_sem_audio_com_extracao_e_recusado(workspace: Workspace, clips: ClipLibrary) -> None:
    workspace.add("mudo.mp4", clips.bytes("mp4_no_audio"))

    item = rejected_item(workspace)

    assert item.code == "AUDIO_STREAM_NOT_FOUND"
    assert workspace.files() == []


def test_video_sem_audio_so_com_miniaturas_e_aceito(
    workspace: Workspace, clips: ClipLibrary
) -> None:
    workspace.add("mudo.mp4", clips.bytes("mp4_no_audio"))

    item = ok_item(workspace, Params(extract_audio=False))

    assert item.outputs == ("thumbnails/mudo-10pct.jpg",)
    assert "audio_stream_index" not in item.details


@pytest.mark.parametrize(
    ("data", "code"),
    [
        pytest.param(HLS_PLAYLIST, "UNSUPPORTED_FORMAT", id="hls-disfarcado"),
        pytest.param(FFCONCAT, "UNSUPPORTED_FORMAT", id="ffconcat"),
        pytest.param(b"", "UNSUPPORTED_FORMAT", id="vazio"),
    ],
)
def test_texto_disfarcado_de_mp4_e_recusado_sem_chamar_o_ffmpeg(
    workspace: Workspace, data: bytes, code: str
) -> None:
    workspace.add("video.mp4", data)
    never_called = Tools(ffmpeg=Path("/nonexistent/ffmpeg"), ffprobe=Path("/nonexistent/ffprobe"))

    report = workspace.run(recipe=MediaMillV1(tools=never_called))

    item = report.items[0]
    assert isinstance(item, ItemRejected)
    assert item.code == code


@pytest.mark.parametrize(
    ("clip", "code"),
    [
        pytest.param("mp4_mpeg4", "UNSUPPORTED_FORMAT", id="codec-fora-da-allowlist"),
        pytest.param("mp4_anamorphic", "UNSUPPORTED_FORMAT", id="pixel-nao-quadrado"),
        pytest.param("mp4_4k", "MEDIA_TOO_LARGE", id="4k"),
        pytest.param("mp4_120fps", "MEDIA_TOO_LARGE", id="120fps"),
        pytest.param("mp4_301s", "MEDIA_TOO_LARGE", id="301s"),
        pytest.param("mp4_six_channels", "AUDIO_LIMIT_EXCEEDED", id="6-canais"),
        pytest.param("mp4_96khz", "AUDIO_LIMIT_EXCEEDED", id="96khz"),
    ],
)
def test_video_hostil_ou_fora_dos_limites_e_recusado(
    workspace: Workspace, clips: ClipLibrary, clip: str, code: str
) -> None:
    workspace.add("video.mp4", clips.bytes(clip))

    item = rejected_item(workspace)

    assert item.code == code
    assert workspace.files() == []


def test_mp4_truncado_sem_moov_e_recusado(workspace: Workspace, clips: ClipLibrary) -> None:
    workspace.add("cortado.mp4", truncated(clips.bytes("mp4")))

    assert rejected_item(workspace).code == "UNSUPPORTED_FORMAT"


def test_mp4_truncado_com_moov_no_inicio_e_recusado_sem_saida_orfa(
    workspace: Workspace, clips: ClipLibrary
) -> None:
    workspace.add("cortado.mp4", truncated(clips.bytes("webm_vp9_opus")))

    item = rejected_item(workspace, Params(thumbnail_count=3))

    assert item.code == "UNSUPPORTED_FORMAT"
    assert workspace.files() == []


def test_audio_truncado_e_recusado(workspace: Workspace, clips: ClipLibrary) -> None:
    workspace.add("cortado.mp4", truncated(clips.bytes("mp4_faststart")))

    item = rejected_item(workspace, Params(thumbnail_count=0))

    assert item.code == "UNSUPPORTED_FORMAT"
    assert workspace.files() == []


def test_video_danificado_no_meio_e_recusado(workspace: Workspace, clips: ClipLibrary) -> None:
    workspace.add("danificado.mp4", corrupted(clips.bytes("mp4_faststart_no_audio")))

    item = rejected_item(workspace, Params(extract_audio=False, thumbnail_count=3))

    assert item.code == "UNSUPPORTED_FORMAT"
    assert workspace.files() == []


def test_lote_continua_depois_de_um_video_recusado(
    workspace: Workspace, clips: ClipLibrary
) -> None:
    workspace.add("ruim.mp4", HLS_PLAYLIST)
    workspace.add("bom.mp4", clips.bytes("mp4"))

    report = workspace.run(Params(thumbnail_count=0))

    assert [type(item) for item in report.items] == [ItemRejected, ItemOk]
    assert workspace.progress.done == [0, 1]
    assert workspace.files() == ["audio/bom.wav"]


def test_metadados_do_video_nao_chegam_as_saidas(workspace: Workspace, clips: ClipLibrary) -> None:
    workspace.add("clip.mp4", clips.bytes("mp4_metadata"))

    ok_item(workspace, Params(audio_format=AudioFormat.FLAC, thumbnail_count=1))

    for name in workspace.files():
        assert CANARY.encode() not in workspace.path(name).read_bytes(), name


def test_mesma_entrada_da_os_mesmos_bytes(clips: ClipLibrary, tmp_path: Path) -> None:
    runs = []
    for attempt in ("a", "b"):
        root = tmp_path / attempt
        root.mkdir()
        workspace = Workspace(root)
        workspace.add("clip.mp4", clips.bytes("mp4"))
        workspace.run(Params(thumbnail_count=3))
        runs.append({name: workspace.path(name).read_bytes() for name in workspace.files()})

    assert runs[0] == runs[1]


def test_nome_com_controle_invisivel_vira_radical_seguro(
    workspace: Workspace, clips: ClipLibrary
) -> None:
    workspace.add("fatura‮gpj.mp4", clips.bytes("mp4"))

    item = ok_item(workspace, Params(thumbnail_count=0))

    assert item.outputs == ("audio/fatura_gpj.wav",)


def test_tempo_esgotado_falha_a_execucao_inteira(workspace: Workspace, clips: ClipLibrary) -> None:
    workspace.add("clip.mp4", clips.bytes("mp4"))
    ticks: Iterator[float] = itertools.count(0.0, 100.0)

    with pytest.raises(RecipeFailure) as failure:
        workspace.run(recipe=MediaMillV1(budget_s=150, clock=lambda: next(ticks)))

    assert failure.value.code is FailureCode.RESOURCE_LIMIT_EXCEEDED


def test_mais_de_cinco_videos_e_job_fora_do_contrato(workspace: Workspace) -> None:
    for index in range(6):
        workspace.add(f"{index}.mp4", HLS_PLAYLIST)

    with pytest.raises(InputContractError):
        workspace.run()
