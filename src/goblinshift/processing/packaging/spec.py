"""O que o empacotador recebe e devolve (docs/GoblinShiftPlan.md, 3.2 e 3.6; ADR 019)."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from goblinshift.processing.recipe import InputFile

MIB = 1024 * 1024
MANIFEST_NAME = "manifest.json"
# Escrito pelo executor antes do empacotamento; entra no ZIP logo depois do manifesto.
REPORT_NAME = "report.json"
MANIFEST_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class PackageSpec:
    """Fatos da execução que o `Report` não tem e o manifesto lista (3.6)."""

    recipe_id: str
    recipe_version: int
    # "<repo>@sha256:<hex>" lido do context.json; None só na janela de deploy (ADR 021).
    executor_image: str | None
    parameters: Mapping[str, object]  # já resolvidos pelo catálogo (o job.json)
    inputs: Sequence[InputFile]

    @property
    def package_name(self) -> str:
        """Nome do ZIP na raiz de out/: "csv-janitor.zip", "image-forge.zip"…"""
        return package_name_for(self.recipe_id)


def package_name_for(recipe_id: str) -> str:
    """Única regra do nome do pacote, usada pelo executor e pela coleta do worker (ADR 019)."""
    return f"{recipe_id}.zip"


@dataclass(frozen=True)
class PackageLimits:
    manifest_max_bytes: int = 8 * MIB  # 1.7: 8 MiB por relatório ou manifesto
    package_max_bytes: int = 512 * MIB  # 1.7: ZIP final até 512 MiB


@dataclass(frozen=True)
class Packaged:
    manifest_name: str
    package_name: str
    entries: int  # arquivos dentro do ZIP (conteúdo, manifesto e relatório)


@dataclass(frozen=True)
class FileDigest:
    size: int  # bytes
    sha256: str  # 64 dígitos hexadecimais minúsculos
