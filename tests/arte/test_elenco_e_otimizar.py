"""elenco.tsv aponta para as seis goblins do Hi3D Quality; otimizar-glb.sh recusa -site.glb.

Sem o marker `arte`: não precisa de Blender nem de ImageMagick, e roda na CI.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts" / "arte"
IDS = ["rubia", "fagulha", "ferrugem", "jade", "lua", "nyx"]
# npx e node falsos: se o script chegar ao `optimize`, o teste falha rápido e sem rede.
FAKE_TOOL = '#!/usr/bin/env bash\necho "$0 não devia rodar" >&2\nexit 97\n'


def _linhas(path: Path) -> list[list[str]]:
    """Linhas do TSV, sem as vazias e sem os comentários."""
    return [
        line.split("\t")
        for line in path.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]


def test_elenco_tem_as_seis_goblins_do_hi3d_quality() -> None:
    rows = _linhas(SCRIPTS / "elenco.tsv")

    assert [r[0] for r in rows] == IDS
    assert all(r[1] == f"hi3d-quality-20261122/{r[0]}-site.glb" for r in rows), rows
    assert all(re.fullmatch(r"[0-9a-f]{32}", r[2]) for r in rows), rows


def test_fonte_ja_otimizada_falha_sem_gravar(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts" / "arte"
    shutil.copytree(SCRIPTS, scripts)
    (tmp_path / "web" / "node_modules" / "meshoptimizer").mkdir(parents=True)
    arte = tmp_path / "arte"
    (arte / "hi3d-quality-20261122").mkdir(parents=True)
    (arte / "hi3d-quality-20261122" / "nyx-site.glb").write_bytes(b"")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool in ("npx", "node"):
        (bin_dir / tool).write_text(FAKE_TOOL)
        (bin_dir / tool).chmod(0o755)

    result = subprocess.run(
        ["bash", str(scripts / "otimizar-glb.sh"), "nyx"],
        env={
            **os.environ,
            "ARTE": str(arte),
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1, result.stderr
    assert "fonte já otimizada para o site" in result.stderr
    out = tmp_path / "web" / "src" / "assets" / "art" / "oficina-3d" / "oficina-3d-nyx.glb"
    assert not out.exists()
