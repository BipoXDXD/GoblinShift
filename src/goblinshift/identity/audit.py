"""Auditoria de eventos de segurança (docs/GoblinShiftPlan.md, 1.8 e 3.6; ADR 026).

Registra login e falha de login, logout e `logout-all`, emissão e revogação de chave,
`REAUTH_REQUIRED`, `CSRF_FAILED`, negações de autorização, exclusão e bloqueio de conta, aceite
dos termos e mudança de limites pelo administrador (`gs-admin set-limits`). Nunca nomes de
arquivo, conteúdo, tokens ou o IP inteiro: o IP é truncado (/24 em IPv4, /48 em IPv6).

Só eventos de quem tem credencial válida (mais o login, já limitado por IP no Nginx) são
auditados: um atacante sem credencial cai no 401, e não consegue inundar a tabela.

O `LOGIN_FAILED` sem conta (callback forjado, falha no provedor) não tem credencial nenhuma: tem
teto global de `MAX_ANONYMOUS_LOGIN_FAILURES_PER_MINUTE` linhas por minuto
(`record_anonymous_login_failure`). A recusa de uma conta conhecida (bloqueada, pendente) é sempre
gravada, porque o operador a acha pelo código de referência no recurso contra o bloqueio. Provedor
desligado é erro de configuração, não tentativa de login, e não é auditado.

O `CSRF_FAILED` é agregado: no máximo uma linha por conta a cada `CSRF_FAILURE_AUDIT_INTERVAL`
(`record_csrf_failure`). Um site hostil faz o navegador da vítima repetir mutações com o cookie
dela, e cada recusa gravaria uma linha; no ritmo da borda, a entrada superaria a retenção.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from ipaddress import IPv4Address, IPv4Network, IPv6Address, IPv6Network, ip_address, ip_network
from typing import Any
from uuid import UUID

from sqlalchemy import ColumnElement, exists, func, insert, literal, null, select
from sqlalchemy.orm import Session

from goblinshift.identity.tables import audit_event

IPV4_PREFIX = 24
IPV6_PREFIX = 48
# Janela da agregação do `CSRF_FAILED`: uma linha por conta neste intervalo.
CSRF_FAILURE_AUDIT_INTERVAL = timedelta(minutes=1)
# Teto global do `LOGIN_FAILED` sem conta (D6 do plano login-e-borda); acima dele, só o log.
MAX_ANONYMOUS_LOGIN_FAILURES_PER_MINUTE = 30
ANONYMOUS_LOGIN_FAILURE_WINDOW = timedelta(minutes=1)


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
            _event_values(
                event=event,
                user_id=user_id,
                ip=ip,
                outcome=outcome,
                now=now,
                request_id=request_id,
                details=details,
            )
        )
    )


def record_csrf_failure(session: Session, *, user_id: UUID, ip: str | None, now: datetime) -> bool:
    """Grava o `CSRF_FAILED` da conta, salvo se já houver um nos últimos
    `CSRF_FAILURE_AUDIT_INTERVAL`; devolve se gravou. Roda na transação de quem chama.

    Um só `INSERT ... SELECT ... WHERE NOT EXISTS`, com as mesmas colunas de `record`
    (`_event_values`). A janela é aberta no início: `occurred_at > now - intervalo`, então a
    falha no minuto exato depois da gravada grava de novo. A busca usa o índice
    `(user_id, occurred_at)`.

    Duas requisições simultâneas da mesma conta podem gravar as duas: nenhuma vê a outra antes do
    commit. A corrida é aceita: o teto vira a concorrência da conta, e o limite de requisições,
    que conta a recusa antes do CSRF, segura o resto.

    Hoje `occurred_at` e a janela vêm do relógio da aplicação (`now`). Quando o `occurred_at`
    passar a vir do banco (build-e-conteineres, T21), a janela também passa a comparar com o
    `now()` do banco: mudar aqui e em `_event_values`, juntos.
    """
    values = _event_values(
        event=AuditEventType.CSRF_FAILED,
        user_id=user_id,
        ip=ip,
        outcome=AuditOutcome.FAILURE,
        now=now,
    )
    recent = (
        select(audit_event.c.id)
        .where(
            audit_event.c.user_id == user_id,
            audit_event.c.event_type == AuditEventType.CSRF_FAILED.value,
            audit_event.c.occurred_at > now - CSRF_FAILURE_AUDIT_INTERVAL,
        )
        .limit(1)
    )
    row = select(*(_as_column(name, value) for name, value in values.items())).where(
        ~exists(recent)
    )
    # RETURNING, e não `rowcount`: o driver devolve -1 no `INSERT ... SELECT`.
    inserted = session.execute(
        insert(audit_event).from_select(list(values), row).returning(audit_event.c.id)
    ).first()
    return inserted is not None


def record_anonymous_login_failure(
    session: Session, *, ip: str | None, request_id: UUID, now: datetime, limit: int
) -> bool:
    """Grava o `LOGIN_FAILED` sem conta, salvo se já houver `limit` deles no último minuto;
    devolve se gravou. Roda na transação de quem chama.

    Um só `INSERT ... SELECT ... WHERE (contagem limitada) < limit`, com as mesmas colunas de
    `record`. A contagem para em `limit` e lê só `user_id IS NULL`: a recusa de conta conhecida
    não gasta o teto. A janela é aberta no início (`occurred_at > now - 1 min`), como a do
    `record_csrf_failure`, e a busca usa o índice de `occurred_at`.

    Requisições simultâneas gravam acima do teto, porque nenhuma vê a outra antes do commit: o
    estouro é limitado pelas requisições em voo (tokens do threadpool vezes processos da API). A
    corrida é aceita, porque o teto protege o tamanho da tabela.
    """
    values = _event_values(
        event=AuditEventType.LOGIN_FAILED,
        user_id=None,
        ip=ip,
        outcome=AuditOutcome.FAILURE,
        now=now,
        request_id=request_id,
    )
    recent = (
        select(literal(1))
        .select_from(audit_event)
        .where(
            audit_event.c.event_type == AuditEventType.LOGIN_FAILED.value,
            audit_event.c.user_id.is_(None),
            audit_event.c.occurred_at > now - ANONYMOUS_LOGIN_FAILURE_WINDOW,
        )
        .limit(limit)
        .subquery()
    )
    recent_count = select(func.count()).select_from(recent).scalar_subquery()
    row = select(*(_as_column(name, value) for name, value in values.items())).where(
        recent_count < limit
    )
    inserted = session.execute(
        insert(audit_event).from_select(list(values), row).returning(audit_event.c.id)
    ).first()
    return inserted is not None


def _event_values(
    *,
    event: AuditEventType,
    user_id: UUID | None,
    ip: str | None,
    outcome: AuditOutcome,
    now: datetime,
    request_id: UUID | None = None,
    details: Mapping[str, object] | None = None,
) -> dict[str, Any]:
    # Um lugar só monta as colunas do evento, para `record` e para os gravados com teto.
    return {
        "user_id": user_id,
        "event_type": event.value,
        "outcome": outcome.value,
        "ip_prefix": truncate_ip(ip),
        "occurred_at": now,
        "request_id": request_id,
        # `null()` e não `None`: no JSONB, `None` grava o JSON `null`, que não é NULL de SQL.
        "details": null() if details is None else details,
    }


def _as_column(name: str, value: Any) -> ColumnElement[Any]:
    # No `INSERT ... SELECT`, cada valor vira um literal com o tipo da coluna de destino.
    if isinstance(value, ColumnElement):
        return value.label(name)
    return literal(value, audit_event.c[name].type).label(name)


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


@dataclass(frozen=True)
class AuditEventView:
    """Um evento de segurança da conta, para a exportação; o IP já é a rede truncada."""

    event_type: AuditEventType
    outcome: AuditOutcome
    occurred_at: datetime
    ip_prefix: IPv4Network | IPv6Network | None
    details: dict[str, Any] | None


def list_audit_events(session: Session, owner_id: UUID) -> list[AuditEventView]:
    """Eventos da conta em ordem cronológica. A retenção de 90 dias (1.8) limita o volume, e o
    `CSRF_FAILED` agregado impede a inundação (`record_csrf_failure`)."""
    rows = session.execute(
        select(
            audit_event.c.event_type,
            audit_event.c.outcome,
            audit_event.c.occurred_at,
            audit_event.c.ip_prefix,
            audit_event.c.details,
        )
        .where(audit_event.c.user_id == owner_id)
        .order_by(audit_event.c.occurred_at, audit_event.c.id)
    ).all()
    return [
        AuditEventView(
            event_type=AuditEventType(row.event_type),
            outcome=AuditOutcome(row.outcome),
            occurred_at=row.occurred_at,
            ip_prefix=row.ip_prefix,
            details=row.details,
        )
        for row in rows
    ]
