"""Uploads citados dentro dos parâmetros de uma receita (PDF Press, 1.5; ADR 035)."""

from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class UploadReference:
    """Upload citado nos parâmetros (o `select` do PDF Press), com o campo que o cita."""

    location: str  # caminho com pontos a partir de `parameters`, como `select.0.upload_id`
    upload_id: UUID


def no_upload_references(_parameters: Mapping[str, object]) -> tuple[UploadReference, ...]:
    """Para as receitas cujos parâmetros não citam uploads."""
    return ()
