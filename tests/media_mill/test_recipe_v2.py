"""Media Mill v2: áudio com perda e corte sobre o mesmo pipeline da v1 (ADR 072).

Usa o FFmpeg do ambiente (`media.py`); o do CI (`apt ffmpeg`) e o do Homebrew trazem LAME e Opus.
"""

from collections.abc import Mapping
from pathlib import Path

import pytest

from goblinshift.processing.media_mill import Params, params_v2_from_mapping
from goblinshift.processing.media_mill.recipe import MediaMillV2
from goblinshift.processing.output_dir import FileSystemOutputDir
from goblinshift.processing.recipe import ItemNoticeCode, ItemOk, ItemRejected, Report
from goblinshift.processing.registry import RECIPES
from tests.media_mill.conftest import ClipLibrary, Workspace
from tests.media_mill.media import CANARY, duration_s, probe

# Um quadro MP3 (1152 amostras) a 44,1 kHz.
MP3_FRAME_S = 1152 / 44100


def run_v2(workspace: Workspace, raw: Mapping[str, object]) -> Report:
    return MediaMillV2().run(
        params_v2_from_mapping(raw),
        workspace.inputs,
        FileSystemOutputDir(workspace.out_dir),
        workspace.progress,
    )


def ok_item(workspace: Workspace, raw: Mapping[str, object]) -> ItemOk:
    item = run_v2(workspace, raw).items[0]
    assert isinstance(item, ItemOk), item
    return item


def milliseconds(item: ItemOk, key: str) -> int:
    value = item.details[key]
    assert isinstance(value, int) and not isinstance(value, bool), (key, value)
    return value


def test_registro_tem_a_v2_ao_lado_da_v1() -> None:
    assert {("media-mill", 1), ("media-mill", 2)} <= RECIPES.keys()


def test_mp3_de_um_trecho_tem_a_duracao_do_trecho(workspace: Workspace, clips: ClipLibrary) -> None:
    workspace.add("férias.mp4", clips.bytes("mp4"))

    item = ok_item(
        workspace,
        {
            "audio_format": "mp3",
            "audio_bitrate_kbps": 128,
            "thumbnail_count": 0,
            "trim_start_ms": 500,
            "trim_end_ms": 1500,
        },
    )

    assert item.outputs == ("audio/férias.mp3",)
    mp3 = workspace.path("audio/férias.mp3")
    assert probe(mp3)["codec_name"] == "mp3"
    # O LAME acrescenta o atraso do encoder e completa o último quadro; sem o cabeçalho Xing, a
    # duração lida inclui os dois (até dois quadros a mais).
    assert 1.0 <= duration_s(mp3) <= 1.0 + 2 * MP3_FRAME_S
    assert (milliseconds(item, "trim_start_ms"), milliseconds(item, "trim_end_ms")) == (500, 1500)
    assert item.details["audio_format"] == "mp3"
    assert milliseconds(item, "audio_bitrate_kbps") == 128
    assert milliseconds(item, "audio_sample_rate_out_hz") == 44100
    assert item.notices == ()


def test_inicio_do_corte_depois_do_fim_recusa_o_item(
    workspace: Workspace, clips: ClipLibrary
) -> None:
    workspace.add("dois.mp4", clips.bytes("mp4"))  # 2 s
    workspace.add("tres.webm", clips.bytes("webm_short_video"))  # 3 s

    report = run_v2(workspace, {"thumbnail_count": 0, "trim_start_ms": 2500})

    first, second = report.items
    assert isinstance(first, ItemRejected)
    assert first.code == "TRIM_OUT_OF_RANGE"
    assert isinstance(second, ItemOk)
    assert milliseconds(second, "trim_start_ms") == 2500


def test_corte_que_comeca_depois_do_fim_da_faixa_de_audio_recusa_o_item(
    workspace: Workspace, clips: ClipLibrary
) -> None:
    # Faixa 0: 1 s de áudio num vídeo de 2 s. O trecho começa em 1,5 s, onde não há áudio.
    workspace.add("faixas.mp4", clips.bytes("mp4_mono_and_stereo"))

    item = run_v2(
        workspace, {"audio_stream_index": 0, "thumbnail_count": 0, "trim_start_ms": 1500}
    ).items[0]

    assert isinstance(item, ItemRejected)
    assert item.code == "TRIM_OUT_OF_RANGE"
    assert workspace.files() == []


def test_fim_do_corte_alem_da_duracao_e_ajustado_com_aviso(
    workspace: Workspace, clips: ClipLibrary
) -> None:
    # PCM de 2 s, sem o padding do AAC: a duração do contêiner é exata.
    workspace.add("clip.mov", clips.bytes("mov_pcm16_mono"))

    item = ok_item(workspace, {"thumbnail_count": 0, "trim_start_ms": 500, "trim_end_ms": 5000})

    assert item.notices == (ItemNoticeCode.MEDIA_TRIM_CLAMPED,)
    assert milliseconds(item, "trim_end_ms") == milliseconds(item, "duration_ms") == 2000
    assert milliseconds(item, "trim_start_ms") == 500
    assert duration_s(workspace.path("audio/clip.wav")) == pytest.approx(1.5, abs=0.001)


def test_corte_sem_fim_vai_ate_o_fim_e_confere_a_faixa(
    workspace: Workspace, clips: ClipLibrary
) -> None:
    workspace.add("clip.mov", clips.bytes("mov_pcm16_mono"))

    item = ok_item(workspace, {"thumbnail_count": 0, "trim_start_ms": 500})

    assert item.notices == ()
    assert (milliseconds(item, "trim_start_ms"), milliseconds(item, "trim_end_ms")) == (500, 2000)
    assert duration_s(workspace.path("audio/clip.wav")) == pytest.approx(1.5, abs=0.001)


def test_miniaturas_caem_dentro_do_intervalo_cortado(
    workspace: Workspace, clips: ClipLibrary
) -> None:
    workspace.add("clip.mp4", clips.bytes("mp4"))

    item = ok_item(
        workspace,
        {"extract_audio": False, "thumbnail_count": 3, "trim_start_ms": 500, "trim_end_ms": 1500},
    )

    assert len(item.outputs) == 3
    assert item.warnings == ()
    requested = [milliseconds(item, f"thumbnail_{p}_requested_ms") for p in ("10", "50", "90")]
    effective = [milliseconds(item, f"thumbnail_{p}_effective_ms") for p in ("10", "50", "90")]
    assert requested == [600, 1000, 1400]
    # O primeiro quadro em ou depois do alvo, a menos de um quadro (1/30 s).
    assert all(r <= e <= r + 34 for r, e in zip(requested, effective, strict=True))


def test_corte_depois_do_fim_do_video_usa_o_ultimo_quadro_com_aviso(
    workspace: Workspace, clips: ClipLibrary
) -> None:
    # Vídeo de 1 s num WebM de 3 s: o trecho começa depois do último quadro, antes do fim.
    workspace.add("curto.webm", clips.bytes("webm_short_video"))

    item = ok_item(workspace, {"extract_audio": False, "thumbnail_count": 1, "trim_start_ms": 1500})

    assert [w.code for w in item.warnings] == ["THUMBNAIL_BEFORE_TARGET"]
    assert milliseconds(item, "thumbnail_10_requested_ms") >= 1500
    assert milliseconds(item, "thumbnail_10_effective_ms") < 1000


@pytest.mark.parametrize(
    ("clip", "raw", "rate", "channels", "notices"),
    [
        pytest.param(
            "mp4_mono_and_stereo",
            {"audio_stream_index": 0, "audio_format": "mp3"},
            "48000",
            "1",
            (ItemNoticeCode.AUDIO_RESAMPLED,),
            id="mp3-de-22050",
        ),
        pytest.param(
            "mp4_mono_and_stereo",
            {"audio_stream_index": 0, "audio_format": "aac", "audio_bitrate_kbps": 256},
            "48000",
            "1",
            (ItemNoticeCode.AUDIO_RESAMPLED,),
            id="aac-mono-256-de-22050",
        ),
        pytest.param(
            "mp4",
            {"audio_format": "opus"},
            "48000",
            "2",
            (ItemNoticeCode.AUDIO_RESAMPLED,),
            id="opus-de-44100",
        ),
        pytest.param("mp4", {"audio_format": "mp3"}, "44100", "2", (), id="mp3-de-44100"),
        pytest.param(
            "mp4_mono_and_stereo",
            {"audio_stream_index": 0},
            "22050",
            "1",
            (),
            id="wav-de-22050",
        ),
    ],
)
def test_taxa_fora_de_44_1_e_48_khz_e_reamostrada_com_aviso(
    workspace: Workspace,
    clips: ClipLibrary,
    clip: str,
    raw: Mapping[str, object],
    rate: str,
    channels: str,
    notices: tuple[ItemNoticeCode, ...],
) -> None:
    workspace.add("clip.mp4", clips.bytes(clip))

    item = ok_item(workspace, {**raw, "thumbnail_count": 0})

    (output,) = item.outputs
    track = probe(workspace.path(output))
    assert (track["sample_rate"], track["channels"]) == (rate, channels)
    assert milliseconds(item, "audio_sample_rate_out_hz") == int(rate)
    assert item.notices == notices


@pytest.mark.parametrize(
    ("audio_format", "suffix"), [("mp3", ".mp3"), ("aac", ".m4a"), ("opus", ".opus")]
)
def test_saida_com_perda_nao_tem_metadados_do_video(
    workspace: Workspace, clips: ClipLibrary, audio_format: str, suffix: str
) -> None:
    workspace.add("clip.mp4", clips.bytes("mp4_metadata"))

    item = ok_item(workspace, {"audio_format": audio_format, "thumbnail_count": 0})

    assert item.outputs == (f"audio/clip{suffix}",)
    assert CANARY.encode() not in workspace.path(item.outputs[0]).read_bytes()


@pytest.mark.parametrize("audio_format", ["mp3", "aac", "opus"])
def test_mesma_entrada_da_os_mesmos_bytes_com_perda(
    clips: ClipLibrary, tmp_path: Path, audio_format: str
) -> None:
    outputs = []
    for attempt in ("a", "b"):
        root = tmp_path / attempt
        root.mkdir()
        workspace = Workspace(root)
        workspace.add("clip.mp4", clips.bytes("mp4"))
        item = ok_item(
            workspace,
            {"audio_format": audio_format, "thumbnail_count": 0, "trim_start_ms": 500},
        )
        outputs.append(workspace.path(item.outputs[0]).read_bytes())

    assert outputs[0] == outputs[1]


def test_v1_nao_ganha_os_campos_da_v2(workspace: Workspace, clips: ClipLibrary) -> None:
    workspace.add("clip.mp4", clips.bytes("mp4"))

    item = workspace.run(Params(thumbnail_count=0)).items[0]

    assert isinstance(item, ItemOk)
    assert (
        not {
            "audio_bitrate_kbps",
            "audio_sample_rate_out_hz",
            "trim_start_ms",
            "trim_end_ms",
        }
        & item.details.keys()
    )
    assert item.notices == ()


def test_v2_sem_corte_nao_registra_o_trecho(workspace: Workspace, clips: ClipLibrary) -> None:
    workspace.add("clip.mp4", clips.bytes("mp4"))

    item = ok_item(workspace, {"thumbnail_count": 0})

    assert not {"trim_start_ms", "trim_end_ms", "audio_bitrate_kbps"} & item.details.keys()
    assert milliseconds(item, "audio_sample_rate_out_hz") == 44100
