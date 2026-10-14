"""Empacotador comum: `manifest.json` e `<recipe_id>.zip` em out/ (docs/GoblinShiftPlan.md, 3.2).

Quem chama é o executor, depois da receita e do `report.json`; nenhuma receita monta ZIP. O que o
empacotador lê do `Report`, congelado na etapa 0 da fatia 5:

- todo arquivo de conteúdo está citado em algum `ItemOk.outputs`, e só ele entra no ZIP;
- a ordem do ZIP e do manifesto é a ordem dos itens (posição da entrada) e, dentro do item, a de
  `outputs`; depois vêm `manifest.json` e `report.json`;
- `ItemDuplicate` aparece no manifesto como duplicata; `ItemRejected`, com código e motivo;
- `warnings` vão para o manifesto; `details` e `counts`, só para o `report.json`.

Esquema do manifesto: `manifest.py`. ZIP determinístico (ordem estável, `date_time` 1980-01-01,
`ZIP_STORED` para formatos já comprimidos): `zip_writer.py`.
"""

from pathlib import Path

from goblinshift.processing.packaging.manifest import encode_manifest
from goblinshift.processing.packaging.spec import (
    MANIFEST_NAME,
    MANIFEST_SCHEMA_VERSION,
    MIB,
    FileDigest,
    Packaged,
    PackageLimits,
    PackageSpec,
)
from goblinshift.processing.packaging.zip_writer import compression_for
from goblinshift.processing.recipe import Report

__all__ = [
    "DEFAULT_LIMITS",
    "MANIFEST_NAME",
    "MANIFEST_SCHEMA_VERSION",
    "MIB",
    "FileDigest",
    "PackageLimits",
    "PackageSpec",
    "Packaged",
    "compression_for",
    "encode_manifest",
    "package",
]


DEFAULT_LIMITS = PackageLimits()


def package(
    report: Report, out_root: Path, spec: PackageSpec, limits: PackageLimits = DEFAULT_LIMITS
) -> Packaged:
    """Grava out/manifest.json e out/<recipe_id>.zip com criação exclusiva.

    Pré-condição: out/report.json já existe. Manifesto acima do teto levanta
    `RecipeFailure(REPORT_LIMIT_EXCEEDED)`; ZIP acima do teto,
    `RecipeFailure(OUTPUT_LIMIT_EXCEEDED)`.
    """
    # Assinatura congelada na etapa 0; a implementação é da frente A, e nada a chama antes dela.
    raise NotImplementedError("package é implementado pela frente A da fatia 5")
