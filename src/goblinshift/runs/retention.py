"""Retenção do histórico de execuções para o processo `maintenance` (docs/GoblinShiftPlan.md, 1.7).

- Parâmetros (`run.parameters`) e código de rejeição por item (`run_input.error_code`) são
  anulados 30 dias depois do término; a Run continua, com estado, contagens e datas. O desfecho
  por item (`ok`/`rejected`) fica, porque só diz quantas entradas foram processadas.
- Run e Attempt são removidas 90 dias depois do término, com entradas, saídas e registros de
  idempotência (FK `CASCADE` a partir da Run).
- `IdempotencyRecord` vencido é removido: a chave só vale 24 h (1.6).

Cada função processa um lote limitado na transação de quem chama, travando com
`FOR UPDATE SKIP LOCKED` para não esperar por API ou worker, e repetir é seguro: o que já foi
feito não entra de novo no filtro. O instante vem de quem chama (o relógio do banco, na
manutenção), o que deixa as fronteiras testáveis.

Ordem segura com o disco: a Run só sai quando nenhuma tentativa dela tem candidato ainda por
descartar. O descarte de candidatos (`Maintenance.discard_stale_candidates`) acha o arquivo pela
tentativa; apagar a tentativa antes deixaria o arquivo sem quem o remova. Artefatos e uploads não
dependem disso: a expiração deles é pela própria linha, e as linhas só saem depois do arquivo
(`files.retention.delete_old_file_rows`).
"""

import uuid
from datetime import datetime, timedelta

from sqlalchemy import ColumnElement, Select, delete, exists, func, null, select, update
from sqlalchemy.orm import Session

from goblinshift.files.objects import release
from goblinshift.files.retention import HistoryReferences, has_unsettled_candidate
from goblinshift.files.states import HolderKind
from goblinshift.runs.tables import attempt, idempotency_record, run, run_input, run_output

ANONYMIZE_AFTER = timedelta(days=30)
DELETE_AFTER = timedelta(days=90)


def _upload_in_history(upload_id: ColumnElement[uuid.UUID]) -> ColumnElement[bool]:
    return exists().where(run_input.c.upload_id == upload_id)


def _artifact_in_history(artifact_id: ColumnElement[uuid.UUID]) -> ColumnElement[bool]:
    return exists().where(run_output.c.artifact_id == artifact_id)


# O que o histórico das execuções ainda referencia, para a limpeza das linhas de `files`.
HISTORY_REFERENCES = HistoryReferences(upload=_upload_in_history, artifact=_artifact_in_history)


def anonymize_finished_runs(session: Session, now: datetime, limit: int) -> int:
    """Anula parâmetros e códigos por item das Runs terminadas há 30 dias ou mais."""
    chosen = list(
        session.execute(
            select(run.c.id)
            .where(
                run.c.parameters.is_not(None),
                run.c.finished_at.is_not(None),
                run.c.finished_at <= now - ANONYMIZE_AFTER,
            )
            .order_by(run.c.finished_at, run.c.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).scalars()
    )
    if not chosen:
        return 0
    # `null()` e não `None`: no JSONB, `None` grava o JSON `null`, que não é NULL de SQL.
    session.execute(update(run).where(run.c.id.in_(chosen)).values(parameters=null()))
    session.execute(
        update(run_input)
        .where(run_input.c.run_id.in_(chosen), run_input.c.error_code.is_not(None))
        .values(error_code=None)
    )
    return len(chosen)


def delete_old_runs(session: Session, now: datetime, limit: int) -> int:
    """Remove as Runs terminadas há 90 dias ou mais cujas tentativas não têm candidato pendente.

    A Run que espera o descarte fica fora do próprio SELECT: o lote segue para as seguintes, em
    vez de escolher de novo as mesmas mais antigas, e ela volta quando o descarte rodar.
    """
    chosen = list(
        session.execute(
            select(run.c.id)
            .where(
                run.c.finished_at.is_not(None),
                run.c.finished_at <= now - DELETE_AFTER,
                ~_waiting_for_discard(run.c.id),
            )
            .order_by(run.c.finished_at, run.c.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).scalars()
    )
    for run_id in chosen:
        # Uso que ficou aberto numa Run terminal prenderia o objeto para sempre sem a Run.
        release(session, HolderKind.RUN, run_id)
    if chosen:
        session.execute(delete(run).where(run.c.id.in_(chosen)))
    return len(chosen)


def delete_expired_idempotency_records(session: Session, now: datetime, limit: int) -> int:
    """Remove registros de idempotência vencidos; o índice de `expires_at` guia o lote."""
    key = (
        idempotency_record.c.owner_id,
        idempotency_record.c.operation,
        idempotency_record.c.idem_key,
    )
    chosen = (
        select(*key)
        .where(idempotency_record.c.expires_at <= now)
        .order_by(idempotency_record.c.expires_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
        .cte("chosen")
    )
    removed = session.execute(
        delete(idempotency_record)
        .where(
            idempotency_record.c.owner_id == chosen.c.owner_id,
            idempotency_record.c.operation == chosen.c.operation,
            idempotency_record.c.idem_key == chosen.c.idem_key,
        )
        .returning(idempotency_record.c.idem_key)
    ).all()
    return len(removed)


def history_backlog(session: Session, now: datetime) -> dict[str, int]:
    """O que ainda falta em cada retenção: a métrica do tamanho do acúmulo (Steady State)."""
    finished = run.c.finished_at.is_not(None)
    return {
        "runs_to_anonymize": _count(
            session,
            select(func.count()).where(
                run.c.parameters.is_not(None), finished, run.c.finished_at <= now - ANONYMIZE_AFTER
            ),
        ),
        "runs_to_delete": _count(
            session,
            select(func.count()).where(finished, run.c.finished_at <= now - DELETE_AFTER),
        ),
        "idempotency_records_expired": _count(
            session,
            select(func.count())
            .select_from(idempotency_record)
            .where(idempotency_record.c.expires_at <= now),
        ),
    }


def _count(session: Session, query: Select[int]) -> int:
    return session.execute(query).scalar_one()


def _waiting_for_discard(run_id: ColumnElement[uuid.UUID]) -> ColumnElement[bool]:
    return exists().where(attempt.c.run_id == run_id, has_unsettled_candidate(attempt.c.id))
