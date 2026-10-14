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

import hashlib
from collections.abc import Sequence
from pathlib import Path

from goblinshift.processing.packaging.manifest import encode_manifest
from goblinshift.processing.packaging.spec import (
    MANIFEST_NAME,
    MANIFEST_SCHEMA_VERSION,
    MIB,
    REPORT_NAME,
    FileDigest,
    Packaged,
    PackageLimits,
    PackageSpec,
    package_name_for,
)
from goblinshift.processing.packaging.zip_writer import (
    COPY_CHUNK_BYTES,
    ZipSource,
    compression_for,
    open_regular,
    write_zip,
)
from goblinshift.processing.recipe import FailureCode, ItemOk, RecipeFailure, Report

__all__ = [
    "DEFAULT_LIMITS",
    "MANIFEST_NAME",
    "MANIFEST_SCHEMA_VERSION",
    "MIB",
    "REPORT_NAME",
    "FileDigest",
    "PackageLimits",
    "PackageSpec",
    "Packaged",
    "compression_for",
    "encode_manifest",
    "package",
    "package_name_for",
]


DEFAULT_LIMITS = PackageLimits()


def package(
    report: Report, out_root: Path, spec: PackageSpec, limits: PackageLimits = DEFAULT_LIMITS
) -> Packaged:
    """Grava out/manifest.json e out/<recipe_id>.zip com criação exclusiva.

    Pré-condição: out/report.json já existe. Manifesto acima do teto levanta
    `RecipeFailure(REPORT_LIMIT_EXCEEDED)`; ZIP acima do teto,
    `RecipeFailure(OUTPUT_LIMIT_EXCEEDED)`, sem deixar o ZIP incompleto em out/.

    São duas passadas sobre as saídas, porque o manifesto entra no ZIP: primeiro o tamanho e o
    SHA-256 de cada uma, depois o ZIP com o conteúdo, o manifesto e o relatório.
    """
    outputs = [path for item in report.items if isinstance(item, ItemOk) for path in item.outputs]
    digests = {path: _digest(out_root / path) for path in outputs}
    manifest = encode_manifest(report, spec, digests)
    if len(manifest) > limits.manifest_max_bytes:
        raise RecipeFailure(
            FailureCode.REPORT_LIMIT_EXCEEDED,
            f"o manifesto teria {len(manifest)} bytes; o limite é {limits.manifest_max_bytes}",
        )
    with (out_root / MANIFEST_NAME).open("xb") as target:
        target.write(manifest)
    sources = [ZipSource(name, out_root / name) for name in (*outputs, MANIFEST_NAME, REPORT_NAME)]
    _write_or_discard(out_root / spec.package_name, sources, limits.package_max_bytes)
    return Packaged(MANIFEST_NAME, spec.package_name, len(sources))


def _digest(path: Path) -> FileDigest:
    digest = hashlib.sha256()
    size = 0
    with open_regular(path) as source:
        while chunk := source.read(COPY_CHUNK_BYTES):
            digest.update(chunk)
            size += len(chunk)
    return FileDigest(size=size, sha256=digest.hexdigest())


def _write_or_discard(target: Path, sources: Sequence[ZipSource], max_bytes: int) -> None:
    """Um ZIP pela metade nunca fica em out/ com o nome do pacote."""
    try:
        write_zip(target, sources, max_bytes)
    except FileExistsError:
        raise  # o arquivo é de outra tentativa: não é nosso para apagar
    except BaseException:
        target.unlink(missing_ok=True)
        raise
