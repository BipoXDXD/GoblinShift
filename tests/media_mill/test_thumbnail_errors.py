"""Miniatura de trecho danificado: o FFmpeg entrega um quadro remendado, mas relata erro (1.5).

O dano que produz exatamente "quadro com erro" depende do bitstream do encoder do ambiente, então
aqui o FFmpeg é um dublê: um script que grava a imagem pedida e escreve no stderr o que o FFmpeg
escreveria. O ffprobe continua o real, sobre um vídeo de verdade.
"""

import stat
import sys
from pathlib import Path

from goblinshift.processing.media_mill import Params
from goblinshift.processing.media_mill.recipe import MediaMillV1
from goblinshift.processing.media_mill.tools import Tools
from goblinshift.processing.recipe import ItemOk, ItemRejected
from tests.media_mill.conftest import ClipLibrary, Workspace
from tests.media_mill.media import require_ffmpeg

SHOWINFO = "[Parsed_showinfo_0 @ 0x1] [info] n:   0 pts:      0 pts_time:0.0 duration: 512"
DECODE_ERROR = "[h264 @ 0x2] [error] Invalid NAL unit size (1515869170 > 2472)."


def fake_ffmpeg(directory: Path, stderr_lines: list[str]) -> Path:
    """Grava um JPEG mínimo no último argumento (o destino) e escreve `stderr_lines`."""
    script = directory / "ffmpeg"
    script.write_text(
        f"#!{sys.executable}\n"
        "import sys\n"
        "open(sys.argv[-1], 'xb').write(b'\\xff\\xd8\\xff\\xd9')\n"
        f"sys.stderr.write({chr(10).join(stderr_lines)!r} + '\\n')\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return script


def run_with(workspace: Workspace, clips: ClipLibrary, tmp_path: Path, lines: list[str]) -> object:
    _, ffprobe = require_ffmpeg()
    workspace.add("clip.mp4", clips.bytes("mp4"))
    tools = Tools(ffmpeg=fake_ffmpeg(tmp_path, lines), ffprobe=Path(ffprobe))
    report = workspace.run(Params(extract_audio=False), MediaMillV1(tools=tools))
    return report.items[0]


def test_quadro_entregue_com_erro_de_decodificacao_recusa_o_video(
    workspace: Workspace, clips: ClipLibrary, tmp_path: Path
) -> None:
    item = run_with(workspace, clips, tmp_path, [DECODE_ERROR, SHOWINFO])

    assert isinstance(item, ItemRejected)
    assert item.code == "UNSUPPORTED_FORMAT"
    assert workspace.files() == []


def test_quadro_entregue_sem_erro_e_aceito(
    workspace: Workspace, clips: ClipLibrary, tmp_path: Path
) -> None:
    item = run_with(workspace, clips, tmp_path, [SHOWINFO])

    assert isinstance(item, ItemOk)
    assert item.details["thumbnail_10_effective_ms"] == item.details["thumbnail_10_requested_ms"]
