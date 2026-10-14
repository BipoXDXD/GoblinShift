"""Piso de segurança do Authlib (ADR 024).

A 1.6.11 corrige o CVE-2026-41425 (state em modo cache sem vínculo com o navegador) e a 1.6.12 é
a correção seguinte; abaixo disso o login por cache no Postgres fica vulnerável a CSRF de login.
"""

import tomllib
from importlib.metadata import version
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FLOOR = (1, 6, 12)


def _as_tuple(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split(".")[:3])


def test_pyproject_exige_o_piso() -> None:
    with (ROOT / "pyproject.toml").open("rb") as source:
        dependencies = tomllib.load(source)["project"]["dependencies"]

    assert "authlib>=1.6.12" in dependencies


def test_lock_e_ambiente_estao_no_piso_ou_acima() -> None:
    with (ROOT / "uv.lock").open("rb") as source:
        locked = {p["name"]: p["version"] for p in tomllib.load(source)["package"]}

    assert _as_tuple(locked["authlib"]) >= FLOOR
    assert _as_tuple(version("authlib")) >= FLOOR
