"""O contexto da imagem web não leva os `.env` da SPA (issue #366, containers f).

`docker/web.Dockerfile.dockerignore` reinclui `web/` inteira, e o `docker/web.Dockerfile` faz
`COPY web/ ./` antes do `npm run build`: um `web/.env.local` da máquina de quem builda entraria
na camada de build, e o Vite o leria. O job `web-image` do CI prova o build real; aqui se confere
só a semântica dos padrões.
"""

import fnmatch
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WEB_DOCKERIGNORE = ROOT / "docker" / "web.Dockerfile.dockerignore"


def _patterns() -> list[str]:
    lines = (line.strip() for line in WEB_DOCKERIGNORE.read_text(encoding="utf-8").splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def _docker_ignored(path: str, patterns: list[str]) -> bool:
    """O último padrão que casa decide; `!` reinclui."""
    ignored = False
    for raw in patterns:
        negated = raw.startswith("!")
        pattern = raw.removeprefix("!")
        prefix = pattern.rstrip("/") + "/"
        if fnmatch.fnmatchcase(path, pattern) or path.startswith(prefix):
            ignored = not negated
    return ignored


@pytest.mark.parametrize("path", ["web/.env", "web/.env.local", "web/.env.production.local"])
def test_arquivos_env_da_spa_ficam_fora_do_contexto(path: str) -> None:
    assert _docker_ignored(path, _patterns()) is True


@pytest.mark.parametrize(
    "path",
    [
        "web/package.json",
        "web/package-lock.json",
        "web/src/main.tsx",
        "web/vendor/picomatch-4.0.7-backport4.tgz",
        "docker/nginx/templates/default.conf.template",
        "docs/legal/termos-de-uso.md",
    ],
)
def test_fontes_da_spa_e_do_nginx_seguem_no_contexto(path: str) -> None:
    assert (ROOT / path).is_file(), f"{path} não existe; o teste perderia o sentido"
    assert _docker_ignored(path, _patterns()) is False


def test_notas_internas_seguem_fora() -> None:
    assert _docker_ignored("docs/legal/notas-internas.md", _patterns()) is True
