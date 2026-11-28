"""Grava o snapshot de schema e defaults de uma versão publicada do catálogo (1.6).

Uso: `uv run python -m scripts.congelar_versao RECIPE_ID VERSION`, uma vez, no PR que publica a
versão. O teste `tests/catalog/test_versoes_congeladas.py` compara o catálogo com os snapshots.

Snapshot existente nunca se sobrescreve: dentro de uma versão, schema e defaults não mudam. Se o
teste falhou porque o código mudou, a saída é uma versão nova, não um snapshot novo.
"""

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from goblinshift.catalog import CATALOG, UnknownRecipeVersionError
from goblinshift.catalog.versions import frozen_view

FROZEN_DIR = Path(__file__).resolve().parent.parent / "tests" / "catalog" / "congeladas"


def main(argv: Sequence[str] | None = None, *, destino: Path = FROZEN_DIR) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("recipe_id")
    parser.add_argument("version", type=int)
    args = parser.parse_args(argv)

    try:
        version = CATALOG.get_version(args.recipe_id, args.version)
    except UnknownRecipeVersionError as error:
        print(f"versão fora do catálogo: {error}", file=sys.stderr)
        return 1

    text = json.dumps(frozen_view(version), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    path = destino / f"{version.recipe_id}-v{version.version}.json"
    try:
        # "x": cria só se não existir, na mesma chamada que abre (sem janela entre checar e gravar).
        with path.open("x", encoding="utf-8") as snapshot:
            snapshot.write(text)
    except FileExistsError:
        print(f"{path.name}: snapshot congelado não se sobrescreve", file=sys.stderr)
        return 1
    print(f"gravado {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
