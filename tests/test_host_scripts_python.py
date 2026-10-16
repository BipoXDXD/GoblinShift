"""Scripts de `deploy/` rodam no Python do host (3.12 no Ubuntu 24.04 e no runner do GitHub).

A aplicação mira 3.14, e o `ruff format` com alvo 3.14 tira os parênteses de `except (A, B):`,
sintaxe que o 3.12 recusa. O alvo 3.12 por pasta fica em `[tool.ruff.per-file-target-version]`.
"""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HOST_PYTHON = (3, 12)
HOST_SCRIPTS = sorted((ROOT / "deploy").rglob("*.py"))


def test_deploy_has_host_scripts() -> None:
    assert HOST_SCRIPTS


@pytest.mark.parametrize("script", HOST_SCRIPTS, ids=lambda p: str(p.relative_to(ROOT)))
def test_host_script_parses_on_host_python(script: Path) -> None:
    ast.parse(script.read_text(), filename=str(script), feature_version=HOST_PYTHON)
