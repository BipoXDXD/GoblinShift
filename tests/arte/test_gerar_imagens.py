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
# Tela de 1400x1750 com a goblin de 1200x1683: o recorte volta a 1400x1750 (1683 * 1,04 = 1750) e
# cobre a maior largura dos estados (1280w) sem ampliar.
BIG_CANVAS = "1400x1750"
BIG_BODY = "rectangle 100,67 1299,1749"
STATE_WIDTHS = [256, 384, 512, 768, 1024, 1280]


def _fake_tools(tmp_path: Path, blender_script: str, canvas: str, body: str) -> tuple[Path, Path]:
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
            canvas,
            "xc:none",
            "-fill",
            "#7a4a2a",
            "-draw",
            body,
            str(render),
        ],
        check=True,
    )
    return bin_dir, render


def _gerar(
    tmp_path: Path,
    *,
    ids: list[str],
    fonte: Path | None,
    alvo: str,
    blender_script: str = FAKE_BLENDER,
    canvas: str = FAKE_CANVAS,
    body: str = FAKE_BODY,
) -> subprocess.CompletedProcess[str]:
    """Roda uma cópia do script em tmp_path, para nada cair em web/src/assets/art/."""
    scripts = tmp_path / "scripts" / "arte"
    shutil.copytree(SCRIPTS, scripts)
    bin_dir, render = _fake_tools(tmp_path, blender_script, canvas, body)
    env = {
        **{k: v for k, v in os.environ.items() if k != "FONTE"},
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "ARTE": str(tmp_path / "arte"),
        "ALVO": alvo,
        "FAKE_RENDER": str(render),
    }
    if fonte is not None:
        env["FONTE"] = str(fonte)
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

    assert result.returncode == 1, result.stderr
    assert "blender falhou (nyx)" in result.stderr
    assert "Traceback: render_glb.py explodiu" in result.stderr


def test_id_fora_do_elenco_falha_sem_processar_nada(tmp_path: Path) -> None:
    fake_glb = tmp_path / "ninguem.glb"
    fake_glb.write_bytes(b"glTF")

    result = _gerar(tmp_path, ids=["ninguem"], fonte=fake_glb, alvo="estados")

    assert result.returncode == 1, result.stderr
    assert "ID fora do elenco.tsv: ninguem" in result.stderr


def _arte_com_nyx(tmp_path: Path) -> None:
    pasta = tmp_path / "arte" / "hi3d-quality-20261122"
    pasta.mkdir(parents=True)
    (pasta / "nyx-site.glb").write_bytes(b"glTF")


def test_id_fora_do_elenco_ao_lado_de_um_valido_falha_sem_gravar(tmp_path: Path) -> None:
    _arte_com_nyx(tmp_path)

    result = _gerar(
        tmp_path,
        ids=["nyx", "ninguem"],
        fonte=None,
        alvo="estados",
        canvas=BIG_CANVAS,
        body=BIG_BODY,
    )

    assert result.returncode == 1, result.stderr
    assert "ID fora do elenco.tsv: ninguem" in result.stderr
    assert not list((tmp_path / "web" / "src" / "assets" / "art" / "goblins").glob("nyx-*"))


def test_caminho_feliz_grava_as_doze_imagens_dos_estados(tmp_path: Path) -> None:
    _arte_com_nyx(tmp_path)

    result = _gerar(
        tmp_path, ids=["nyx"], fonte=None, alvo="estados", canvas=BIG_CANVAS, body=BIG_BODY
    )

    assert result.returncode == 0, result.stderr
    assert "nyx: ok" in result.stdout
    goblins = tmp_path / "web" / "src" / "assets" / "art" / "goblins"
    expected = {f"nyx-{w}w.{ext}" for w in STATE_WIDTHS for ext in ("avif", "webp")}
    assert {p.name for p in goblins.glob("nyx-*")} == expected


# Blender falso que anota os argumentos de cada chamada antes de copiar o render.
LOGGING_BLENDER = """#!/usr/bin/env bash
set -euo pipefail
echo "$*" >>"$FAKE_RENDER.calls"
while [[ "$1" != "--" ]]; do shift; done
cp "$FAKE_RENDER" "$3"
"""


@pytest.mark.parametrize("alvo", ["estados", "tudo"])
def test_estados_usam_um_render_realista_so(tmp_path: Path, alvo: str) -> None:
    # Decisão do dono em 2026-11-22: os estados das seis saem do render realista, como os da Rúbia,
    # e a imagem da cena reaproveita o mesmo render (uma abertura do Blender por goblin).
    _arte_com_nyx(tmp_path)

    result = _gerar(
        tmp_path,
        ids=["nyx"],
        fonte=None,
        alvo=alvo,
        blender_script=LOGGING_BLENDER,
        canvas=BIG_CANVAS,
        body=BIG_BODY,
    )

    assert result.returncode == 0, result.stderr
    calls = (tmp_path / "render.png.calls").read_text().splitlines()
    assert len(calls) == 1
    assert calls[0].endswith("--realista")
