"""render_glb.py no Blender de verdade (marker `arte`, fora do CI e da rodada padrão).

O CLI (argumentos e código de saída) é testado sem o Blender em test_render_glb_args.py.
"""

import re
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.arte

REPO = Path(__file__).resolve().parents[2]
RENDER = REPO / "scripts" / "arte" / "render_glb.py"
SITE_GLB = REPO / "web" / "src" / "assets" / "art" / "oficina-3d" / "oficina-3d-nyx.glb"
# DSSIM normalizado (0 = iguais). Os dois modos mudam motor, tom e luz: a diferença passa longe.
MIN_MODE_DSSIM = 0.01
RENDER_PX = 256
NO_ALPHA = {"Undefined", "False"}
DSSIM_NORMALIZED = re.compile(r"\(([0-9.e+-]+)\)")


def _blender(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "blender",
            "-b",
            "--factory-startup",
            "--python-exit-code",
            "1",
            "-P",
            str(RENDER),
            "--",
            *args,
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def _render(glb: Path, out: Path, px: int, *extra: str) -> None:
    result = _blender(str(glb), str(out), str(px), *extra)
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]


def _identify(png: Path) -> tuple[int, int, bool]:
    """Largura, altura e se o PNG tem canal alfa."""
    result = subprocess.run(
        ["magick", "identify", "-format", "%w %h %A", str(png)],
        capture_output=True,
        text=True,
        check=True,
    )
    width, height, alpha = result.stdout.split()
    return int(width), int(height), alpha not in NO_ALPHA


def _dssim(a: Path, b: Path) -> float:
    # `compare` sai com 1 quando as imagens diferem; a métrica vai para o stderr.
    result = subprocess.run(
        ["magick", "compare", "-metric", "DSSIM", str(a), str(b), "null:"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode in {0, 1}, result.stderr
    match = DSSIM_NORMALIZED.search(result.stderr)
    assert match, result.stderr
    return float(match.group(1))


@pytest.fixture(scope="module")
def renders(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """Os dois modos a 256 px: duas aberturas do Blender para o módulo inteiro."""
    out = tmp_path_factory.mktemp("renders")
    paths = {"padrao": out / "padrao.png", "realista": out / "realista.png"}
    _render(SITE_GLB, paths["padrao"], RENDER_PX)
    _render(SITE_GLB, paths["realista"], RENDER_PX, "--realista")
    return paths


def test_render_padrao_grava_png_quadrado_transparente(renders: dict[str, Path]) -> None:
    assert _identify(renders["padrao"]) == (RENDER_PX, RENDER_PX, True)


def test_render_realista_difere_do_padrao(renders: dict[str, Path]) -> None:
    assert _dssim(renders["padrao"], renders["realista"]) > MIN_MODE_DSSIM
