"""Mudança dos limites de uma conta pelo administrador (`gs-admin set-limits`; 1.7).

Os limites são as cotas diárias (`account_quota`, override das settings `GS_QUOTA_*`) e o teto de
armazenamento do pool da conta (livro-razão, ADR 024). A mudança e o evento
`ACCOUNT_LIMITS_CHANGED` entram na transação de quem chama, juntos ou nenhum dos dois.

O override das cotas é ler e regravar as duas colunas: dois comandos simultâneos na mesma conta
perderiam uma mudança e auditariam `old` errado. Um advisory lock de transação por conta,
como em `presets.store`, serializa o comando inteiro antes de qualquer leitura; vale também
quando a conta ainda não tem linha em `account_quota`, onde um `FOR UPDATE` não teria o que travar.
O segundo espera no máximo o `lock_timeout` de 2 s do `make_engine` e falha alto se passar dele.
"""

from collections.abc import Mapping
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from goblinshift import capacity
from goblinshift.identity import (
    AuditEventType,
    AuditOutcome,
    LimitChange,
    limits_changed_details,
    record_audit_event,
)
from goblinshift.runs.quotas import quota_override, set_account_quota

RUNS_PER_24H = "runs_per_24h"
INPUT_BYTES_PER_24H = "input_bytes_per_24h"
STORAGE_BYTES = "storage_bytes"
LIMIT_ITEMS = (RUNS_PER_24H, INPUT_BYTES_PER_24H, STORAGE_BYTES)
_LOCK_NAMESPACE = "goblinshift.runs.limits.owner"


def change_account_limits(
    session: Session, owner_id: UUID, limits: Mapping[str, int | None], now: datetime
) -> None:
    """Aplica os limites citados (`None` volta o item ao padrão) e audita antes e depois.

    As chaves são as de `LIMIT_ITEMS`; o que não for citado fica como está. Teto de armazenamento
    abaixo do já reservado levanta `capacity.StorageBelowReservedError` sem mudar nada, desde que
    quem chama desfaça a transação.
    """
    unknown = set(limits) - set(LIMIT_ITEMS)
    if not limits or unknown:
        raise ValueError(f"limites inválidos: {sorted(unknown) or 'nenhum citado'}")
    session.execute(
        select(func.pg_advisory_xact_lock(func.hashtext(f"{_LOCK_NAMESPACE}.{owner_id}")))
    )
    changes: dict[str, LimitChange] = {}
    quotas = {name: value for name, value in limits.items() if name != STORAGE_BYTES}
    if quotas:
        current = quota_override(session, owner_id)
        previous = {
            RUNS_PER_24H: current.runs_per_24h,
            INPUT_BYTES_PER_24H: current.input_bytes_per_24h,
        }
        set_account_quota(session, owner_id, **{**previous, **quotas})
        changes |= {name: LimitChange(previous[name], new) for name, new in quotas.items()}
    if STORAGE_BYTES in limits:
        old_max = capacity.set_account_storage_max(session, owner_id, limits[STORAGE_BYTES])
        new_max = capacity.usage(session, owner_id).storage_max_bytes
        changes[STORAGE_BYTES] = LimitChange(old_max, new_max)
    # Ação do administrador no servidor: não há IP de cliente a registrar.
    record_audit_event(
        session,
        event=AuditEventType.ACCOUNT_LIMITS_CHANGED,
        user_id=owner_id,
        ip=None,
        outcome=AuditOutcome.SUCCESS,
        now=now,
        details=limits_changed_details(changes),
    )
