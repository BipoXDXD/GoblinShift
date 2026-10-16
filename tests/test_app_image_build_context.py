"""O build da imagem da aplicação enxerga tudo o que o pyproject.toml exige.

O `uv sync` do app.Dockerfile constrói o projeto, e o backend de build recusa metadados cujo
`license-files` não acha os arquivos. Eles precisam passar pelo .dockerignore (que é allowlist) e
ser copiados antes do sync.
"""

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _license_files() -> list[str]:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    files: list[str] = project.get("license-files", [])
    return files


def test_o_pyproject_declara_arquivos_de_licenca() -> None:
    assert _license_files(), "sem license-files este teste não protege nada"


def test_arquivos_de_licenca_passam_pelo_dockerignore() -> None:
    allowed = {
        line.strip().removeprefix("!").rstrip("/")
        for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip().startswith("!")
    }
    assert set(_license_files()) <= allowed


def test_arquivos_de_licenca_sao_copiados_antes_do_build_do_projeto() -> None:
    dockerfile = (ROOT / "docker" / "app.Dockerfile").read_text(encoding="utf-8")
    build_stage = dockerfile.split("uv sync --locked --no-dev --no-editable")[0]
    copies = [line.split()[1:-1] for line in build_stage.splitlines() if line.startswith("COPY ")]
    copied = {path for args in copies for path in args}
    assert set(_license_files()) <= copied
