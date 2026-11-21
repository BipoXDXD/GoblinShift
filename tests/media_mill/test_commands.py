"""Linhas de comando do FFmpeg e do ffprobe: o que 2.3 exige em toda chamada (ADR 036)."""

from pathlib import Path

import pytest

from goblinshift.processing.media_mill import AudioFormat
from goblinshift.processing.media_mill.commands import (
    Box,
    Source,
    audio_argv,
    last_frame_before_argv,
    probe_argv,
    read_log,
    thumbnail_argv,
)
from goblinshift.processing.media_mill.signature import Container

FFMPEG = Path("/usr/local/bin/ffmpeg")
FFPROBE = Path("/usr/local/bin/ffprobe")
INPUT = Path("/work/inputs/0000.bin")
TARGET = Path("/work/tmp/x/out.jpg")


def all_commands(container: Container) -> dict[str, list[str]]:
    source = Source(INPUT, container)
    box = Box(640, 360)
    return {
        "probe": probe_argv(FFPROBE, source),
        "audio": audio_argv(
            FFMPEG,
            source,
            stream_order=1,
            sample_rate=48000,
            channels=2,
            audio_format=AudioFormat.FLAC,
            cut_at_s=3.0,
            target=TARGET,
        ),
        "thumbnail": thumbnail_argv(FFMPEG, source, at_s=1.5, box=box, target=TARGET),
        "before": last_frame_before_argv(FFMPEG, source, until_s=2.7, box=box, target=TARGET),
    }


def option(argv: list[str], name: str) -> str:
    return argv[argv.index(name) + 1]


@pytest.mark.parametrize("container", list(Container))
@pytest.mark.parametrize("command", ["probe", "audio", "thumbnail", "before"])
def test_toda_leitura_so_aceita_arquivo_e_forca_o_demuxer_da_assinatura(
    container: Container, command: str
) -> None:
    argv = all_commands(container)[command]

    assert option(argv, "-protocol_whitelist") == "file"
    assert option(argv, "-f") == container.demuxer
    assert option(argv, "-i") == "file:/work/inputs/0000.bin"
    # As opções de entrada vêm antes do `-i`, onde valem para a leitura.
    assert argv.index("-protocol_whitelist") < argv.index("-i")
    assert argv.index("-f") < argv.index("-i")


@pytest.mark.parametrize("command", ["audio", "thumbnail", "before"])
def test_ffmpeg_sem_stdin_numa_thread_sem_metadados_e_sem_sobrescrever(command: str) -> None:
    argv = all_commands(Container.MOV)[command]

    assert argv[0] == str(FFMPEG)
    assert "-nostdin" in argv
    assert option(argv, "-threads") == "1"
    assert option(argv, "-filter_threads") == "1"
    assert option(argv, "-map_metadata") == "-1"
    assert option(argv, "-map_chapters") == "-1"
    assert argv[-2:] == ["-n", str(TARGET)]


@pytest.mark.parametrize("command", ["probe", "audio", "thumbnail", "before"])
def test_toda_leitura_decodifica_numa_thread(command: str) -> None:
    argv = all_commands(Container.MOV)[command]
    assert option(argv, "-threads") == "1"
    assert argv.index("-threads") < argv.index("-i")


def test_mov_nao_segue_referencias_a_outros_arquivos() -> None:
    for argv in all_commands(Container.MOV).values():
        assert option(argv, "-enable_drefs") == "0"


def test_webm_nao_recebe_opcao_do_mov() -> None:
    for argv in all_commands(Container.WEBM).values():
        assert "-enable_drefs" not in argv


def test_audio_preserva_taxa_e_canais_e_fixa_16_bits() -> None:
    argv = all_commands(Container.MOV)["audio"]

    assert (option(argv, "-map"), option(argv, "-ar"), option(argv, "-ac")) == (
        "0:a:1",
        "48000",
        "2",
    )
    assert (option(argv, "-c:a"), option(argv, "-sample_fmt")) == ("flac", "s16")


def test_wav_e_pcm_de_16_bits() -> None:
    argv = audio_argv(
        FFMPEG,
        Source(INPUT, Container.MOV),
        stream_order=0,
        sample_rate=44100,
        channels=1,
        audio_format=AudioFormat.WAV,
        cut_at_s=3.0,
        target=TARGET,
    )

    assert (option(argv, "-c:a"), argv[argv.index("-c:a") + 3]) == ("pcm_s16le", "wav")


def test_audio_corta_um_segundo_depois_da_duracao_aceita_e_registra_os_quadros() -> None:
    argv = audio_argv(
        FFMPEG,
        Source(INPUT, Container.MOV),
        stream_order=0,
        sample_rate=48000,
        channels=2,
        audio_format=AudioFormat.WAV,
        cut_at_s=12.5,
        target=TARGET,
    )

    assert option(argv, "-t") == "12.500000"
    assert option(argv, "-af") == "ashowinfo"
    # Nível `info`: é nele que o `ashowinfo` escreve cada quadro.
    assert option(argv, "-loglevel") == "level+info"
    assert argv.index("-i") < argv.index("-t") < argv.index("-n")
    assert argv.index("-i") < argv.index("-af") < argv.index("-n")


def test_miniatura_busca_o_alvo_e_grava_um_jpeg_na_caixa() -> None:
    argv = all_commands(Container.MOV)["thumbnail"]

    assert option(argv, "-ss") == "1.500000"
    assert argv.index("-ss") < argv.index("-i")
    assert option(argv, "-frames:v") == "1"
    assert option(argv, "-c:v") == "mjpeg"
    assert "scale=640:360:flags=lanczos" in option(argv, "-vf")


def test_ultimo_quadro_anterior_le_ate_o_alvo() -> None:
    argv = all_commands(Container.MOV)["before"]

    assert option(argv, "-t") == "2.700000"
    assert "-ss" not in argv
    assert "-frames:v" not in argv


def test_entrada_relativa_e_recusada() -> None:
    with pytest.raises(ValueError, match="absoluto"):
        Source(Path("inputs/0000.bin"), Container.MOV)


def test_log_separa_erros_e_instantes_dos_quadros(tmp_path: Path) -> None:
    log = tmp_path / "x.log"
    log.write_bytes(
        b"[in#0 @ 0x1] [info] Stream #0:0: Video: h264\n"
        b"[Parsed_showinfo_0 @ 0x2] [info] n:   0 pts:  15360 pts_time:1       duration: 512\n"
        b"[Parsed_showinfo_0 @ 0x2] [info] n:   1 pts:  15872 pts_time:1.033333 duration: 512\n"
        b"[h264 @ 0x3] [warning] deprecated pixel format used\n"
    )

    summary = read_log(log)

    assert (summary.has_errors, summary.first_frame_s, summary.last_frame_s) == (
        False,
        1.0,
        1.033333,
    )


@pytest.mark.parametrize("level", [b"error", b"fatal", b"panic"])
def test_log_com_erro_e_marcado(tmp_path: Path, level: bytes) -> None:
    log = tmp_path / "x.log"
    log.write_bytes(b"[mov @ 0x1] [" + level + b"] stream 1, offset 0x1cf40: partial file\n")

    assert read_log(log).has_errors


def test_log_sem_quadros(tmp_path: Path) -> None:
    log = tmp_path / "x.log"
    log.write_bytes(b"")

    summary = read_log(log)

    assert (summary.has_errors, summary.first_frame_s, summary.last_frame_s) == (False, None, None)
    assert (summary.frame_count, summary.max_long_side_px, summary.max_short_side_px) == (0, 0, 0)


def test_log_guarda_o_instante_do_ultimo_quadro_de_audio(tmp_path: Path) -> None:
    log = tmp_path / "x.log"
    log.write_bytes(
        b"[Parsed_ashowinfo_0 @ 0x1] [info] n:0 pts:0 pts_time:0 fmt:s16\n"
        b"[Parsed_ashowinfo_0 @ 0x1] [info] n:1 pts:1024 pts_time:2.98 fmt:s16\n"
    )

    summary = read_log(log)

    assert (summary.last_audio_s, summary.first_frame_s) == (2.98, None)


def test_log_sem_quadros_de_audio(tmp_path: Path) -> None:
    log = tmp_path / "x.log"
    log.write_bytes(b"[Parsed_showinfo_0 @ 0x2] [info] n:   0 pts:  15360 pts_time:1\n")

    assert read_log(log).last_audio_s is None


def test_pts_time_fora_do_showinfo_e_ignorado(tmp_path: Path) -> None:
    log = tmp_path / "x.log"
    log.write_bytes(b"[other @ 0x1] [info] pts_time:9\n")

    assert read_log(log).first_frame_s is None


def test_log_guarda_o_maior_quadro_e_conta_os_quadros(tmp_path: Path) -> None:
    log = tmp_path / "x.log"
    log.write_bytes(
        b"[Parsed_showinfo_0 @ 0x2] [info] n:0 pts:0 pts_time:0 s:320x240 fmt:yuv420p\n"
        b"[Parsed_showinfo_0 @ 0x2] [info] n:1 pts:512 pts_time:1 s:1440x2560 fmt:yuv420p\n"
    )

    summary = read_log(log)

    assert (summary.frame_count, summary.max_long_side_px, summary.max_short_side_px) == (
        2,
        2560,
        1440,
    )


def test_so_linhas_de_quadro_do_showinfo_contam(tmp_path: Path) -> None:
    # Formato real do FFmpeg 9: linhas de configuração e de cor do mesmo filtro, sem `pts_time`,
    # e o `ashowinfo`, que não é quadro de vídeo.
    log = tmp_path / "x.log"
    log.write_bytes(
        b"[Parsed_showinfo_0 @ 0x2] [info] config in time_base: 1/30, frame_rate: 30/1\n"
        b"[Parsed_showinfo_0 @ 0x2] [info] n:   0 pts:      0 pts_time:0       duration:      1"
        b" duration_time:0.0333333 fmt:yuv420p cl:unspecified sar:1/1 s:320x240 i:P iskey:1\n"
        b"[Parsed_showinfo_0 @ 0x2] [info] color_range:unknown color_space:unknown\n"
        b"[Parsed_ashowinfo_0 @ 0x3] [info] n:0 pts:0 pts_time:0 fmt:s16 channels:1\n"
    )

    summary = read_log(log)

    assert (summary.frame_count, summary.max_long_side_px, summary.max_short_side_px) == (
        1,
        320,
        240,
    )


def test_texto_do_usuario_no_log_nao_vira_quadro(tmp_path: Path) -> None:
    # O título da faixa aparece no log como `[info]   title : ...`, e uma quebra de linha nele
    # vira linha de continuação com espaços na frente: nenhuma começa com o nome do filtro.
    log = tmp_path / "x.log"
    log.write_bytes(
        b"[info]     title           : Parsed_showinfo pts_time:7 s:9999x9999\n"
        b"[Parsed_showinfo_0 @ 0x2] [info] n:   0 pts:      0 pts_time:1 s:320x240\n"
        b"[Parsed_ashowinfo_0 @ 0x3] [info] n:0 pts:0 pts_time:0.1 fmt:s16\n"
        b"[info]     title           : Parsed_ashowinfo pts_time:0.2\n"
        b"                    : [Parsed_showinfo_0 @ 0x1] [info] n:0 pts_time:8 s:9999x9999\n"
    )

    summary = read_log(log)

    assert (summary.first_frame_s, summary.last_frame_s, summary.last_audio_s) == (1.0, 1.0, 0.1)
    assert (summary.frame_count, summary.max_long_side_px, summary.max_short_side_px) == (
        1,
        320,
        240,
    )
