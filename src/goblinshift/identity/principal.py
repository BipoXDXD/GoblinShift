"""Quem fez a requisição e o que pode fazer (docs/GoblinShiftPlan.md, 3.2 e 3.6).

Os módulos de negócio recebem só o `owner_id` já autenticado; o `Principal` fica nos
entrypoints, que decidem a autorização por escopo antes de chamar os serviços.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal
from uuid import UUID

SCOPE_SEPARATOR = ","


class Scope(StrEnum):
    """Escopos de chave de API da tabela de rotas de 1.6.

    `presets:*` entra com os presets; o banco guarda texto, então acrescentar um escopo
    não exige migração.
    """

    FILES_READ = "files:read"
    FILES_WRITE = "files:write"
    RUNS_READ = "runs:read"
    RUNS_WRITE = "runs:write"


@dataclass(frozen=True)
class Principal:
    owner_id: UUID
    scopes: frozenset[Scope]
    # Só chaves na fatia 3; a sessão web ("session") chega na fatia 6.
    via: Literal["api_key"]


class InvalidScopesError(ValueError):
    pass


def parse_scopes(text: str) -> frozenset[Scope]:
    """Lê escopos separados por vírgula; vazio ou desconhecido é erro, nunca ignorado."""
    names = [name.strip() for name in text.split(SCOPE_SEPARATOR) if name.strip()]
    if not names:
        raise InvalidScopesError("informe pelo menos um escopo")
    unknown = sorted(set(names) - {scope.value for scope in Scope})
    if unknown:
        valid = ", ".join(scope.value for scope in Scope)
        raise InvalidScopesError(f"escopos desconhecidos: {unknown}; válidos: {valid}")
    return frozenset(Scope(name) for name in names)
