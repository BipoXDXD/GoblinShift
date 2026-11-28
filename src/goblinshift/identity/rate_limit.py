"""Limite de requisições por conta, canal e classe de rota (GoblinShiftPlan.md, 1.7; ADR 028).

Janela fixa de um minuto, em UTC, contada no PostgreSQL: vale entre réplicas sem Redis (ADR 003).
Cada requisição é um único `INSERT … ON CONFLICT DO UPDATE … WHERE request_count < limite`, que
cria a janela ou soma um, sob a trava da linha. Sem linha devolvida, o limite já foi atingido:
nenhuma corrida passa dele, nem na primeira requisição da janela.

A janela fixa admite até o dobro do limite em rajada na virada do minuto; o limite protege a VPS
de uma conta só, não mede vazão exata.

O canal (`RequestChannel`, o `Principal.via`) separa os baldes da sessão web e das chaves de API:
uma chave que esgota a conta não tranca o titular fora da sessão que a lista e revoga (#442,
49a). O teto da conta no máximo dobra, qualquer que seja o número de chaves ou sessões.
"""

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Final, Literal
from uuid import UUID

from sqlalchemy import Table, delete, func, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from goblinshift.identity.tables import channel_request_window, legacy_request_window
from goblinshift.platform.sql import skip_locked_batch

WINDOW: Final = timedelta(minutes=1)

# O `Principal.via`: as chaves da conta dividem um balde, e a sessão web tem o seu.
type RequestChannel = Literal["session", "api_key"]


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
    session: Session,
    owner_id: UUID,
    channel: RequestChannel,
    request_class: RequestClass,
    now: datetime,
) -> None:
    """Conta a requisição na janela atual do canal ou levanta `RequestLimitExceededError`.

    Roda numa transação própria e curta de quem chama: a contagem fica gravada mesmo se a
    requisição falhar depois.
    """
    window_start = _window_start(now)
    table = channel_request_window
    statement = (
        insert(table)
        .values(
            owner_id=owner_id,
            channel=channel,
            request_class=request_class.value,
            window_start=window_start,
            request_count=1,
        )
        .on_conflict_do_update(
            index_elements=[
                table.c.owner_id,
                table.c.channel,
                table.c.request_class,
                table.c.window_start,
            ],
            set_={"request_count": table.c.request_count + 1},
            where=table.c.request_count < REQUESTS_PER_MINUTE[request_class],
        )
        .returning(table.c.request_count)
    )
    if session.execute(statement).first() is None:
        raise RequestLimitExceededError(request_class, window_start + WINDOW - now)


def delete_expired_windows(session: Session, now: datetime, limit: int) -> int:
    """Apaga até `limit` janelas anteriores à anterior; devolve quantas apagou.

    A janela anterior fica: uma réplica com o relógio um pouco atrasado ainda conta nela. Limpa
    também a `request_window` da 0007, que só a réplica anterior ao deploy ainda grava, até o PR
    que a apaga (preflight R4); o lote vale para as duas juntas.
    """
    previous_window = _window_start(now) - WINDOW
    removed = _delete_expired(session, channel_request_window, previous_window, limit)
    if removed < limit:
        removed += _delete_expired(session, legacy_request_window, previous_window, limit - removed)
    return removed


def count_windows(session: Session) -> int:
    """Janelas guardadas nas duas tabelas: a métrica de tamanho que a manutenção registra."""
    total = 0
    for table in (channel_request_window, legacy_request_window):
        counted: int = session.execute(select(func.count()).select_from(table)).scalar_one()
        total += counted
    return total


def _delete_expired(session: Session, table: Table, previous_window: datetime, limit: int) -> int:
    # A chave primária inteira identifica a linha; a da 0007 não tem `channel`.
    key_columns = [column for column in table.primary_key.columns]
    chosen = skip_locked_batch(
        select(*key_columns).where(table.c.window_start < previous_window), limit
    )
    chosen_keys = select(*(chosen.c[column.name] for column in key_columns))
    deleted = session.execute(
        delete(table).where(tuple_(*key_columns).in_(chosen_keys)).returning(table.c.owner_id)
    )
    return len(deleted.all())


def _window_start(now: datetime) -> datetime:
    return now.astimezone(UTC).replace(second=0, microsecond=0)
