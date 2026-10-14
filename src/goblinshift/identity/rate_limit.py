"""Limite de requisições por conta e classe de rota (docs/GoblinShiftPlan.md, 1.7; ADR 027).

Janela fixa de um minuto, em UTC, contada no PostgreSQL: vale entre réplicas sem Redis (ADR 003).
Cada requisição é um único `INSERT … ON CONFLICT DO UPDATE … WHERE request_count < limite`, que
cria a janela ou soma um, sob a trava da linha. Sem linha devolvida, o limite já foi atingido:
nenhuma corrida passa dele, nem na primeira requisição da janela.

A janela fixa admite até o dobro do limite em rajada na virada do minuto; o limite protege a VPS
de uma conta só, não mede vazão exata.
"""

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Final
from uuid import UUID

from sqlalchemy import delete, func, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from goblinshift.identity.tables import request_window

WINDOW: Final = timedelta(minutes=1)


class RequestClass(StrEnum):
    """Classes de rota com limite por minuto; o envio e o download de conteúdo não entram."""

    READ = "read"
    COMMAND = "command"
    RUN_CREATE = "run_create"


REQUESTS_PER_MINUTE: Final[dict[RequestClass, int]] = {
    RequestClass.READ: 120,
    RequestClass.COMMAND: 60,
    RequestClass.RUN_CREATE: 10,
}


class RequestLimitExceededError(Exception):
    """A conta atingiu o limite da classe nesta janela; `retry_after` é o tempo até a seguinte."""

    def __init__(self, request_class: RequestClass, retry_after: timedelta) -> None:
        super().__init__(f"limite de {request_class.value} atingido; nova janela em {retry_after}")
        self.request_class = request_class
        self.retry_after = retry_after


def count_request(
    session: Session, owner_id: UUID, request_class: RequestClass, now: datetime
) -> None:
    """Conta a requisição na janela atual ou levanta `RequestLimitExceededError`.

    Roda numa transação própria e curta de quem chama: a contagem fica gravada mesmo se a
    requisição falhar depois.
    """
    window_start = _window_start(now)
    statement = (
        insert(request_window)
        .values(
            owner_id=owner_id,
            request_class=request_class.value,
            window_start=window_start,
            request_count=1,
        )
        .on_conflict_do_update(
            index_elements=[
                request_window.c.owner_id,
                request_window.c.request_class,
                request_window.c.window_start,
            ],
            set_={"request_count": request_window.c.request_count + 1},
            where=request_window.c.request_count < REQUESTS_PER_MINUTE[request_class],
        )
        .returning(request_window.c.request_count)
    )
    if session.execute(statement).first() is None:
        raise RequestLimitExceededError(request_class, window_start + WINDOW - now)


def delete_expired_windows(session: Session, now: datetime, limit: int) -> int:
    """Apaga até `limit` janelas anteriores à anterior; devolve quantas apagou.

    A janela anterior fica: uma réplica com o relógio um pouco atrasado ainda conta nela.
    """
    previous_window = _window_start(now) - WINDOW
    key = tuple_(
        request_window.c.owner_id, request_window.c.request_class, request_window.c.window_start
    )
    chosen = (
        select(
            request_window.c.owner_id,
            request_window.c.request_class,
            request_window.c.window_start,
        )
        .where(request_window.c.window_start < previous_window)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    deleted = session.execute(
        delete(request_window).where(key.in_(chosen)).returning(request_window.c.owner_id)
    )
    return len(deleted.all())


def count_windows(session: Session) -> int:
    """Janelas guardadas: a métrica do tamanho da tabela que a manutenção registra."""
    total: int = session.execute(select(func.count()).select_from(request_window)).scalar_one()
    return total


def _window_start(now: datetime) -> datetime:
    return now.astimezone(UTC).replace(second=0, microsecond=0)
