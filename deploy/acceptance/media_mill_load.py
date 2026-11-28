"""Pior caso do Media Mill sob gVisor, antes de publicar a versão (ADR 036).

Roda na VPS, como root, a partir de um checkout temporário atual (README, "Checkout temporário na
VPS"; nunca da cópia parada em /srv/goblinshift/app), com o worker parado, sobre os vídeos que
`scripts/media-mill-worst-case.sh` gera numa máquina com o FFmpeg completo (a VPS não tem FFmpeg,
e a imagem do executor só tem o build mínimo, que não codifica H.264):

    cd "$gs_tmp/repo/deploy" && sudo python3 -B -m acceptance.media_mill_load <dir-dos-videos>

Sem opções, constrói a imagem no host: minutos de CPU por causa do FFmpeg, pesado na VPS que atende
a produção. Para medir uma imagem já publicada, retague-a para `goblinshift-executor:medicao` e use
`--skip-build` (README, "Medir com a imagem publicada").

Reaproveita a medição do Image Forge (`image_forge_load.measure`): usa a imagem do executor do
checkout e roda uma tentativa de verdade com as MESMAS opções do perfil de produção (runsc, 1 vCPU,
2 GiB, sem rede), com a manutenção parada (ADR 017). Pede áudio em FLAC e três miniaturas por
vídeo; com `--version 2`, Opus a 192 kbps sem corte (o codificador mais caro da v2, ADR 063).
Passa se a tentativa terminar com código 0, todos os vídeos aceitos, dentro de 600 s.
Usa só a biblioteca padrão (Python 3.12 do host).
"""

import argparse
import hashlib
import os
import sys
from pathlib import Path
from typing import Any

from acceptance import image_forge_load as load
from acceptance.run import Host
from executor_ctl.config import DEFAULT_CONFIG_PATH, load_config

MAX_VIDEOS = 5
VIDEO_SUFFIXES = (".mp4", ".mov", ".webm")
# Parâmetros já resolvidos, como o catálogo os grava no job (ADR 036).
PARAMETERS = {
    "audio_format": "flac",
    "audio_stream_index": None,
    "extract_audio": True,
    "thumbnail_count": 3,
}
CRITERION = "5 vídeos de 300 s em 1080p60 com FLAC e 3 miniaturas numa tentativa (teto de 600 s)"
PARAMETERS_V2 = {
    "audio_bitrate_kbps": 192,
    "audio_format": "opus",
    "audio_stream_index": None,
    "extract_audio": True,
    "thumbnail_count": 3,
    "trim_end_ms": None,
    "trim_start_ms": 0,
}
CRITERION_V2 = (
    "5 vídeos de 300 s em 1080p60 com Opus a 192 kbps e 3 miniaturas, v2 sem corte, numa "
    "tentativa (teto de 600 s)"
)
_BY_VERSION = {1: (PARAMETERS, CRITERION), 2: (PARAMETERS_V2, CRITERION_V2)}


def expected_for(videos: Path, version: int = 1) -> dict[str, Any]:
    """O equivalente ao expected.json do gerador, dos vídeos do diretório em ordem de nome."""
    paths = sorted(p for p in videos.iterdir() if p.suffix.casefold() in VIDEO_SUFFIXES)
    if not 1 <= len(paths) <= MAX_VIDEOS:
        raise ValueError(f"esperados de 1 a {MAX_VIDEOS} vídeos em {videos}, há {len(paths)}")
    return {
        "recipe_id": "media-mill",
        "recipe_version": version,
        "parameters": _BY_VERSION[version][0],
        "inputs": [
            {
                "path": path.name,
                "manifest": {
                    "position": position,
                    "original_name": path.name,
                    "size": path.stat().st_size,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                },
            }
            for position, path in enumerate(paths)
        ],
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python3 -B -m acceptance.media_mill_load")
    parser.add_argument("videos", type=Path, help="diretório com os vídeos do pior caso")
    parser.add_argument(
        "--version", type=int, choices=sorted(_BY_VERSION), default=1, help="versão da receita"
    )
    load.add_skip_build(parser)
    options = parser.parse_args(argv)
    if os.geteuid() != 0:
        print("rode com sudo", file=sys.stderr)
        return 2
    videos = options.videos.resolve()
    expected = expected_for(videos, options.version)
    criterion = _BY_VERSION[options.version][1]
    config = load_config(DEFAULT_CONFIG_PATH)
    host = Host.from_config(config)
    if options.skip_build:
        load.require_image()
    else:
        load.build_image()
    return load.measure_with_maintenance_suspended(
        lambda: load.measure(host, videos, expected, criterion)
    )


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
