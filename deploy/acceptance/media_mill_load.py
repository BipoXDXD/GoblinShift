"""Pior caso do Media Mill sob gVisor, antes de publicar a versão (ADR 036).

Roda na VPS, como root, a partir do checkout da branch, com o worker parado, sobre os vídeos que
`scripts/media-mill-worst-case.sh` gera numa máquina com o FFmpeg completo (a VPS não tem FFmpeg,
e a imagem do executor só tem o build mínimo, que não codifica H.264):

    cd /srv/goblinshift/app/deploy && sudo python3 -B -m acceptance.media_mill_load <dir-dos-videos>

Reaproveita a medição do Image Forge (`image_forge_load.measure`): constrói a imagem do executor do
checkout e roda uma tentativa de verdade com as MESMAS opções do perfil de produção (runsc, 1 vCPU,
2 GiB, sem rede), com a manutenção parada (ADR 017). Pede áudio em FLAC e três miniaturas por
vídeo. Passa se a tentativa terminar com código 0, todos os vídeos aceitos, dentro de 600 s.
Usa só a biblioteca padrão (Python 3.12 do host).
"""

import grp
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


def expected_for(videos: Path) -> dict[str, Any]:
    """O equivalente ao expected.json do gerador, dos vídeos do diretório em ordem de nome."""
    paths = sorted(p for p in videos.iterdir() if p.suffix.casefold() in VIDEO_SUFFIXES)
    if not 1 <= len(paths) <= MAX_VIDEOS:
        raise ValueError(f"esperados de 1 a {MAX_VIDEOS} vídeos em {videos}, há {len(paths)}")
    return {
        "recipe_id": "media-mill",
        "recipe_version": 1,
        "parameters": PARAMETERS,
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
    if len(argv) != 1:
        print("uso: python3 -B -m acceptance.media_mill_load <dir-dos-videos>", file=sys.stderr)
        return 2
    if os.geteuid() != 0:
        print("rode com sudo", file=sys.stderr)
        return 2
    videos = Path(argv[0]).resolve()
    expected = expected_for(videos)
    config = load_config(DEFAULT_CONFIG_PATH)
    host = Host(config, grp.getgrnam(config.group).gr_gid)
    print(f"construindo {load.IMAGE} a partir de {load.REPO_ROOT}")
    load.build_image()
    return load.measure_with_maintenance_suspended(
        lambda: load.measure(host, videos, expected, CRITERION)
    )


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
