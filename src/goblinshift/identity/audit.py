"""Auditoria de eventos de segurança (docs/GoblinShiftPlan.md, 1.8 e 3.6; ADR 026).

Registra login e falha de login, logout e `logout-all`, emissão e revogação de chave,
`REAUTH_REQUIRED`, `CSRF_FAILED`, negações de autorização, exclusão e bloqueio de conta, aceite
dos termos e mudança de limites pelo administrador (`gs-admin set-limits`). Nunca nomes de
arquivo, conteúdo, tokens ou o IP inteiro: o IP é truncado (/24 em IPv4, /48 em IPv6).

Só eventos de quem tem credencial válida (mais o login, já limitado por IP no Nginx) são
auditados: um atacante sem credencial cai no 401, e não consegue inundar a tabela.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from ipaddress import IPv4Address, IPv4Network, IPv6Address, IPv6Network, ip_address, ip_network
from uuid import UUID

from sqlalchemy import insert, null
from sqlalchemy.orm import Session

from goblinshift.identity.tables import audit_event

IPV4_PREFIX = 24
IPV6_PREFIX = 48


class AuditEventType(StrEnum):
    """Tipos aceitos pelo CHECK `audit_event_event_type_check` (migrações 0006, 0008 e 0016)."""

    LOGIN_SUCCEEDED = "LOGIN_SUCCEEDED"
    LOGIN_FAILED = "LOGIN_FAILED"
    LOGOUT = "LOGOUT"
    LOGOUT_ALL = "LOGOUT_ALL"
    KEY_ISSUED = "KEY_ISSUED"
    KEY_REVOKED = "KEY_REVOKED"
    REAUTH_REQUIRED = "REAUTH_REQUIRED"
    CSRF_FAILED = "CSRF_FAILED"
    ACCESS_DENIED = "ACCESS_DENIED"
    ACCOUNT_DELETION_REQUESTED = "ACCOUNT_DELETION_REQUESTED"
    ACCOUNT_BLOCKED = "ACCOUNT_BLOCKED"
    ACCOUNT_UNBLOCKED = "ACCOUNT_UNBLOCKED"
    TERMS_ACCEPTED = "TERMS_ACCEPTED"
    # O único com `details` (CHECK `audit_event_details_check`): ver `LimitChange`.
    ACCOUNT_LIMITS_CHANGED = "ACCOUNT_LIMITS_CHANGED"


class AuditOutcome(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"


@dataclass(frozen=True)
class LimitChange:
    """Um limite citado no `set-limits`, antes e depois; `None` nas cotas é "sem override"."""

    old: int | None
    new: int | None


def limits_changed_details(changes: Mapping[str, LimitChange]) -> dict[str, object]:
    """O `details` do `ACCOUNT_LIMITS_CHANGED`: `{"changes": {<item>: {"old", "new"}}}`.

    Os itens são os nomes da saída do `gs-admin usage` (`runs_per_24h`, `input_bytes_per_24h`,
    `storage_bytes`), só os citados no comando. A conta-alvo é o `user_id` do evento.
    """
    return {
        "changes": {
            item: {"old": change.old, "new": change.new} for item, change in changes.items()
        }
    }


def record(
    session: Session,
    *,
    event: AuditEventType,
    user_id: UUID | None,
    ip: str | None,
    outcome: AuditOutcome,
    now: datetime,
    request_id: UUID | None = None,
    details: Mapping[str, object] | None = None,
) -> None:
    """Grava o evento na transação de quem chama.

    `details` só no tipo que o exige (`ACCOUNT_LIMITS_CHANGED`); o CHECK do banco recusa o resto.

    `request_id` liga o evento ao `X-Request-ID` da resposta; o login o grava para o operador
    achar a conta pelo código da tela de login recusado.

    Uma recusa (CSRF, 403) desfaz a transação da requisição; quem a audita abre uma transação
    própria para que o evento sobreviva ao rollback.
    """
    session.execute(
        insert(audit_event).values(
            user_id=user_id,
            event_type=event.value,
            outcome=outcome.value,
            ip_prefix=truncate_ip(ip),
            occurred_at=now,
            request_id=request_id,
            # `null()` e não `None`: no JSONB, `None` grava o JSON `null`, que não é NULL de SQL.
            details=null() if details is None else details,
        )
    )


def truncate_ip(ip: str | None) -> IPv4Network | IPv6Network | None:
    """Rede /24 (IPv4) ou /48 (IPv6) do cliente; ausente ou ilegível vira None.

    O endereço vem de `request.client.host`, que o uvicorn preenche a partir do
    `X-Forwarded-For` só quando a borda é confiável. Um valor ilegível não impede o registro do
    evento, que vale mais que o IP.
    """
    if ip is None:
        return None
    try:
        address = ip_address(ip.strip())
    except ValueError:
        return None
    # IPv4 mapeado em IPv6 (socket dual-stack) é o mesmo cliente IPv4.
    if isinstance(address, IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    prefix = IPV4_PREFIX if isinstance(address, IPv4Address) else IPV6_PREFIX
    return ip_network(f"{address}/{prefix}", strict=False)
