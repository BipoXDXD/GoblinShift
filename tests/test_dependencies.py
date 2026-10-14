"""Pillow fixado numa versão exata, a mesma no projeto, no grupo do executor e no lock (ADR 017).

A imagem do executor instala o grupo `executor` a partir do `uv.lock`; o ambiente de testes usa
`[project.dependencies]`. Se os dois divergirem, os testes passam com uma versão e a produção roda
outra.
"""

import tomllib
from pathlib import Path

import PIL

ROOT = Path(__file__).resolve().parent.parent
PILLOW_PIN = "pillow==12.3.0"


def _pyproject() -> dict[str, object]:
    with (ROOT / "pyproject.toml").open("rb") as source:
        return tomllib.load(source)


def _locked_versions() -> dict[str, str]:
    with (ROOT / "uv.lock").open("rb") as source:
        lock = tomllib.load(source)
    return {package["name"]: package["version"] for package in lock["package"]}


def test_pillow_tem_pin_exato_nas_dependencias_do_projeto() -> None:
    project = _pyproject()["project"]
    assert isinstance(project, dict)

    pillow = [dep for dep in project["dependencies"] if dep.startswith("pillow")]

    assert pillow == [PILLOW_PIN]


def test_grupo_do_executor_tem_so_o_mesmo_pin_do_pillow() -> None:
    groups = _pyproject()["dependency-groups"]
    assert isinstance(groups, dict)

    assert groups["executor"] == [PILLOW_PIN]


def test_lock_e_ambiente_tem_a_versao_fixada() -> None:
    assert _locked_versions()["pillow"] == "12.3.0"
    assert PIL.__version__ == "12.3.0"
