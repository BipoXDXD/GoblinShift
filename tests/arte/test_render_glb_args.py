"""CLI do render_glb.py sem abrir o Blender: o bpy e o mathutils entram como módulos falsos."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parents[2]
RENDER = REPO / "scripts" / "arte" / "render_glb.py"


@pytest.fixture
def render_glb(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    mathutils = ModuleType("mathutils")
    mathutils.__dict__["Vector"] = tuple
    monkeypatch.setitem(sys.modules, "bpy", ModuleType("bpy"))
    monkeypatch.setitem(sys.modules, "mathutils", mathutils)
    spec = importlib.util.spec_from_file_location("render_glb", RENDER)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_argumento_faltando_sai_com_2(
    render_glb: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        render_glb.parse_args(["in.glb"])

    assert exit_info.value.code == 2
    assert "required: OUT.png, PX" in capsys.readouterr().err


@pytest.mark.parametrize("px", ["0", "-5", "grande"])
def test_px_invalido_sai_com_2(
    render_glb: ModuleType, capsys: pytest.CaptureFixture[str], px: str
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        render_glb.parse_args(["in.glb", "out.png", px])

    assert exit_info.value.code == 2
    assert "argument PX:" in capsys.readouterr().err


def test_sem_opcoes_gira_20_graus_no_modo_padrao(render_glb: ModuleType) -> None:
    options = render_glb.parse_args(["in.glb", "out.png", "512"])

    assert (options.source, options.out, options.px) == ("in.glb", "out.png", 512)
    assert (options.yaw, options.realistic) == (20.0, False)


def test_yaw_e_realista_sao_lidos(render_glb: ModuleType) -> None:
    options = render_glb.parse_args(["in.glb", "out.png", "256", "--yaw=-35", "--realista"])

    assert (options.yaw, options.realistic) == (-35.0, True)
