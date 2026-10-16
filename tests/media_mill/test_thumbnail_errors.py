"""Miniatura de trecho danificado: o FFmpeg entrega um quadro remendado, mas relata erro (1.5).

O dano que produz exatamente "quadro com erro" depende do bitstream do encoder do ambiente, então
aqui o FFmpeg é um dublê: um script que grava a imagem pedida e escreve no stderr o que o FFmpeg
escreveria. O ffprobe continua o real, sobre um vídeo de verdade.
"""

import stat
import sys
from pathlib import Path
from typing import Any

import pytest

from goblinshift.processing.media_mill import Params
from goblinshift.processing.media_mill.recipe import CallTimeouts, MediaMillV1
from goblinshift.processing.media_mill.tools import Tools
from goblinshift.processing.recipe import FailureCode, ItemOk, ItemRejected, RecipeFailure
from tests.media_mill.conftest import ClipLibrary, Workspace
from tests.media_mill.media import require_ffmpeg

SHOWINFO = "[Parsed_showinfo_0 @ 0x1] [info] n:   0 pts:      0 pts_time:0.0 duration: 512"
DECODE_ERROR = "[h264 @ 0x2] [error] Invalid NAL unit size (1515869170 > 2472)."


def fake_ffmpeg(directory: Path, stderr_lines: list[str], sleep_s: float = 0) -> Path:
    """Grava um JPEG mínimo no último argumento (o destino) e escreve `stderr_lines`."""
    script = directory / "ffmpeg"
    script.write_text(
        f"#!{sys.executable}\n"
        "import sys, time\n"
        f"time.sleep({sleep_s})\n"
        "open(sys.argv[-1], 'xb').write(b'\\xff\\xd8\\xff\\xd9')\n"
        f"sys.stderr.write({chr(10).join(stderr_lines)!r} + '\\n')\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return script


def run_with(
    workspace: Workspace,
    clips: ClipLibrary,
    tmp_path: Path,
    lines: list[str],
    *,
    sleep_s: float = 0,
    recipe_options: dict[str, Any] | None = None,
) -> object:
    _, ffprobe = require_ffmpeg()
    workspace.add("clip.mp4", clips.bytes("mp4"))
    tools = Tools(ffmpeg=fake_ffmpeg(tmp_path, lines, sleep_s), ffprobe=Path(ffprobe))
    recipe = MediaMillV1(tools=tools, **(recipe_options or {}))
    report = workspace.run(Params(extract_audio=False), recipe)
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


def test_video_que_passa_do_teto_de_uma_chamada_e_recusado_e_o_lote_segue(
    workspace: Workspace, clips: ClipLibrary, tmp_path: Path
) -> None:
    # Um GOP enorme (um keyframe só em 300 s de 1080p60) faz a busca precisa decodificar milhares
    # de quadros: o teto por chamada recusa esse vídeo, sem derrubar a execução inteira.
    timeouts = CallTimeouts(probe_s=30, audio_s=30, thumbnail_s=0.5)

    item = run_with(
        workspace, clips, tmp_path, [SHOWINFO], sleep_s=5, recipe_options={"timeouts": timeouts}
    )

    assert isinstance(item, ItemRejected)
    assert item.code == "MEDIA_TOO_LARGE"
    assert workspace.files() == []


def test_orcamento_da_execucao_esgotado_numa_chamada_falha_a_execucao(
    workspace: Workspace, clips: ClipLibrary, tmp_path: Path
) -> None:
    with pytest.raises(RecipeFailure) as failure:
        run_with(
            workspace, clips, tmp_path, [SHOWINFO], sleep_s=5, recipe_options={"budget_s": 1.5}
        )

    assert failure.value.code is FailureCode.RESOURCE_LIMIT_EXCEEDED
