"""Estágio `ffmpeg` do docker/executor.Dockerfile lido como texto (ADR 036 e ADR 063).

Os testes `executor_image` (tests/test_executor_image.py) conferem o binário, mas só rodam no
job `gvisor` da CI, porque constroem a imagem. Estes leem o `configure` e os `ARG` do estágio e
rodam em qualquer lugar: LGPL sem `--enable-gpl` nem `--enable-nonfree`, LAME e Opus fixados por
versão e SHA-256, estáticos, e só os encoders e muxers que as receitas usam.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXECUTOR_DOCKERFILE = ROOT / "docker" / "executor.Dockerfile"
SHA256_HEX = re.compile(r"[0-9a-f]{64}")


def _ffmpeg_stage() -> str:
    """O texto do estágio `ffmpeg`, do `FROM ... AS ffmpeg` até o `FROM` seguinte."""
    text = EXECUTOR_DOCKERFILE.read_text(encoding="utf-8")
    match = re.search(r"^FROM \S+ AS ffmpeg\n(.*?)^FROM ", text, re.MULTILINE | re.DOTALL)
    assert match, "estágio ffmpeg não encontrado"
    return match.group(1)


def _args(stage: str) -> dict[str, str]:
    return dict(re.findall(r"^ARG (\w+)=(\S+)$", stage, re.MULTILINE))


def _ffmpeg_configure(stage: str) -> list[str]:
    """As opções do `./configure` do FFmpeg, uma por item (continuações de linha unidas)."""
    match = re.search(r"\./configure --prefix=/opt/ffmpeg(.*?)&& make", stage, re.DOTALL)
    assert match, "configure do FFmpeg não encontrado"
    return match.group(1).replace("\\\n", " ").split()


def _enabled(options: list[str], kind: str) -> set[str]:
    prefix = f"--enable-{kind}="
    (listed,) = [option.removeprefix(prefix) for option in options if option.startswith(prefix)]
    return set(listed.split(","))


def test_ffmpeg_continua_sem_gpl_nem_nonfree() -> None:
    options = _ffmpeg_configure(_ffmpeg_stage())

    assert "--enable-gpl" not in options
    assert "--enable-nonfree" not in options
    assert "--enable-version3" not in options


def test_lame_e_opus_tem_versao_e_sha256_fixados() -> None:
    args = _args(_ffmpeg_stage())

    assert args["LAME_VERSION"] == "3.100"
    assert args["OPUS_VERSION"] == "1.6.1"
    for name in ("LAME_SHA256", "OPUS_SHA256", "OPUS_DEBIAN_SHA256"):
        assert SHA256_HEX.fullmatch(args[name]), name


def test_lame_e_opus_entram_estaticos_no_ffmpeg() -> None:
    stage = _ffmpeg_stage()
    options = _ffmpeg_configure(stage)

    assert stage.count("--disable-shared --enable-static --prefix=/opt/codecs") == 2
    assert {"--enable-libmp3lame", "--enable-libopus"} <= set(options)
    assert "--extra-ldflags=-L/opt/codecs/lib" in options


def test_ffmpeg_so_habilita_os_encoders_e_muxers_das_receitas() -> None:
    options = _ffmpeg_configure(_ffmpeg_stage())

    assert _enabled(options, "encoder") == {
        "mjpeg",
        "pcm_s16le",
        "flac",
        "aac",
        "libmp3lame",
        "libopus",
    }
    assert _enabled(options, "muxer") == {"wav", "flac", "image2", "mp3", "ipod", "opus"}
    assert "asetpts" in _enabled(options, "filter")
