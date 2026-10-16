"""Quem fez a requisição e o que pode fazer (docs/GoblinShiftPlan.md, 3.2 e 3.6).

Os módulos de negócio recebem só o `owner_id` já autenticado; o `Principal` fica nos
entrypoints, que decidem a autorização por escopo antes de chamar os serviços.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

SCOPE_SEPARATOR = ","


class Scope(StrEnum):
    """Escopos de chave de API da tabela de rotas de 1.6.

    O banco guarda texto, então acrescentar um escopo não exige migração.
    """

    FILES_READ = "files:read"
    FILES_WRITE = "files:write"
    RUNS_READ = "runs:read"
    RUNS_WRITE = "runs:write"
    PRESETS_READ = "presets:read"
    PRESETS_WRITE = "presets:write"


@dataclass(frozen=True)
class Principal:
    """Credencial já autenticada: sessão web ou chave de API.

    A sessão usa as permissões da conta, isto é, todos os escopos (1.6); a chave, só os seus.
    `session_id` e `authenticated_at` (login no provedor, base da autenticação recente de 10 min)
    existem só na sessão.
    """

    owner_id: UUID
    scopes: frozenset[Scope]
    via: Literal["session", "api_key"]
    session_id: UUID | None = None
    authenticated_at: datetime | None = None

    def __post_init__(self) -> None:
        has_session_data = self.session_id is not None or self.authenticated_at is not None
        if self.via == "api_key" and has_session_data:
            raise ValueError("credencial de chave não tem sessão nem login no provedor")
        if self.via == "session" and (self.session_id is None or self.authenticated_at is None):
            raise ValueError("credencial de sessão exige session_id e authenticated_at")
        if self.authenticated_at is not None and self.authenticated_at.tzinfo is None:
            raise ValueError("authenticated_at precisa de fuso horário")


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
