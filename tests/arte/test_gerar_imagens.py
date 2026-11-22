"""gerar-imagens.sh: recusa ampliar, mostra o erro do Blender e falha quando nada é processado."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.arte

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts" / "arte"
# Blender falso: copia $FAKE_RENDER para o PNG de saída (o segundo argumento depois de `--`).
FAKE_BLENDER = """#!/usr/bin/env bash
set -euo pipefail
while [[ "$1" != "--" ]]; do shift; done
cp "$FAKE_RENDER" "$3"
"""
# Blender falso que quebra como um script Python com erro sob --python-exit-code 1.
BROKEN_BLENDER = """#!/usr/bin/env bash
echo "Traceback: render_glb.py explodiu" >&2
exit 1
"""
# Tela de 600x750 com a goblin falsa de 500x722 encostada embaixo: o recorte justo com 4% de folga
# volta a 600x750 (722 * 1,04 = 750; 750 * 4/5 = 600).
FAKE_CANVAS = "600x750"
FAKE_BODY = "rectangle 50,28 549,749"


def _fake_tools(tmp_path: Path, blender_script: str) -> tuple[Path, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    blender = bin_dir / "blender"
    blender.write_text(blender_script)
    blender.chmod(0o755)
    render = tmp_path / "render.png"
    subprocess.run(
        [
            "magick",
            "-size",
            FAKE_CANVAS,
            "xc:none",
            "-fill",
            "#7a4a2a",
            "-draw",
            FAKE_BODY,
            str(render),
        ],
        check=True,
    )
    return bin_dir, render


def _gerar(
    tmp_path: Path,
    *,
    ids: list[str],
    fonte: Path,
    alvo: str,
    blender_script: str = FAKE_BLENDER,
) -> subprocess.CompletedProcess[str]:
    """Roda uma cópia do script em tmp_path, para nada cair em web/src/assets/art/."""
    scripts = tmp_path / "scripts" / "arte"
    shutil.copytree(SCRIPTS, scripts)
    bin_dir, render = _fake_tools(tmp_path, blender_script)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "ARTE": str(tmp_path / "arte"),
        "ALVO": alvo,
        "FONTE": str(fonte),
        "FAKE_RENDER": str(render),
    }
    return subprocess.run(
        ["bash", str(scripts / "gerar-imagens.sh"), *ids],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_largura_maior_que_o_recorte_falha_sem_gravar(tmp_path: Path) -> None:
    fake_glb = tmp_path / "nyx-site.glb"
    fake_glb.write_bytes(b"glTF")

    result = _gerar(tmp_path, ids=["nyx"], fonte=fake_glb, alvo="estados")

    assert result.returncode == 1, result.stderr
    assert "fonte pequena demais: 600 px para 1280w (nyx)" in result.stderr
    assert not list((tmp_path / "web" / "src" / "assets" / "art" / "goblins").glob("nyx-*"))


def test_erro_do_blender_aparece_no_stderr_com_o_id(tmp_path: Path) -> None:
    fake_glb = tmp_path / "nyx-site.glb"
    fake_glb.write_bytes(b"glTF")

    result = _gerar(
        tmp_path, ids=["nyx"], fonte=fake_glb, alvo="estados", blender_script=BROKEN_BLENDER
    )

    assert result.returncode != 0
    assert "blender falhou (nyx)" in result.stderr
    assert "Traceback: render_glb.py explodiu" in result.stderr


def test_id_fora_do_elenco_falha_sem_processar_nada(tmp_path: Path) -> None:
    fake_glb = tmp_path / "ninguem.glb"
    fake_glb.write_bytes(b"glTF")

    result = _gerar(tmp_path, ids=["ninguem"], fonte=fake_glb, alvo="estados")

    assert result.returncode == 1, result.stderr
    assert "nenhum ID do elenco.tsv processado: ninguem" in result.stderr
