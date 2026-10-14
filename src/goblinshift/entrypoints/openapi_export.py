"""Processo `gs-export-openapi`: grava o contrato OpenAPI da API para a interface web (fatia 7).

O arquivo versionado (`web/openapi/openapi.json`) é a fonte dos tipos TypeScript gerados em
`web/src/api/schema.d.ts`. A aplicação é montada sem banco, como nos testes offline, e a versão do
contrato vem do pacote, não do deploy: o mesmo código sempre gera os mesmos bytes, então o CI
compara o arquivo com o export e falha se alguém mudou a API sem regenerar.

Rode na raiz do repositório: `uv run gs-export-openapi [caminho]`.
"""

import json
import sys
from collections.abc import Sequence
from importlib.metadata import version
from pathlib import Path

from goblinshift.entrypoints.api.app import create_app
from goblinshift.platform.settings import Settings

DEFAULT_TARGET = Path("web/openapi/openapi.json")
EXIT_OK = 0
EXIT_USAGE = 2


def render_openapi() -> str:
    settings = Settings(environment="test", service_version=version("goblinshift"), runner="local")
    spec = create_app(settings).openapi()
    return json.dumps(spec, indent=2, ensure_ascii=False) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) > 1:
        print("uso: gs-export-openapi [caminho]", file=sys.stderr)
        return EXIT_USAGE
    target = Path(args[0]) if args else DEFAULT_TARGET
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_openapi(), encoding="utf-8")
    return EXIT_OK
