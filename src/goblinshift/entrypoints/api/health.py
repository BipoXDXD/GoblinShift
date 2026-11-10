"""Sinais de vida e de prontidão (docs/GoblinShiftPlan.md, 3.8).

`/livez` só diz que o processo responde. `/readyz` depende apenas do banco, coerente com
3.5: sem banco não há como aceitar, consultar nem baixar nada. As respostas não expõem
detalhes internos, porque o monitor de uptime externo também as consulta; o motivo de
um "not_ready" vai para o log.
"""

import logging
from collections.abc import Callable

from fastapi import APIRouter, Request, Response
from opentelemetry.instrumentation.utils import suppress_instrumentation
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from goblinshift.platform.schema import read_db_heads, schema_is_ready

logger = logging.getLogger(__name__)

router = APIRouter()

# Validade do resultado do /readyz. O Nginx limita as sondas por cliente; isto limita o banco a
# uma verificação por segundo por processo, mesmo com sondas de muitos clientes ao mesmo tempo.
READINESS_TTL_SECONDS = 1.0


class ReadinessCache:
    """Último resultado da verificação de prontidão, reaproveitado por `READINESS_TTL_SECONDS`.

    Sem lock: duas threads que chegam com o resultado vencido podem verificar as duas, ao custo
    de uma consulta a mais. Exceção inesperada da verificação não é guardada.
    """

    def __init__(self, check: Callable[[], bool], *, clock: Callable[[], float]) -> None:
        self._check = check
        self._clock = clock
        self._last: tuple[float, bool] | None = None

    def is_ready(self) -> bool:
        now = self._clock()
        last = self._last
        if last is not None and now - last[0] < READINESS_TTL_SECONDS:
            return last[1]
        ready = self._check()
        self._last = (now, ready)
        return ready


def database_is_ready(
    engine: Engine, code_heads: frozenset[str], known_revisions: frozenset[str]
) -> bool:
    try:
        # As sondas ficam fora dos traces, como a própria rota em `excluded_urls`: sem requisição
        # exportada, cada consulta daqui viraria um trace sem pai, várias vezes por minuto.
        with suppress_instrumentation(), engine.connect() as conn:
            db_heads = read_db_heads(conn)
    except SQLAlchemyError:
        logger.warning("readyz: banco indisponível", exc_info=True)
        return False

    if not schema_is_ready(db_heads, code_heads, known_revisions):
        logger.warning(
            "readyz: schema incompatível (banco=%s, código=%s)",
            sorted(db_heads),
            sorted(code_heads),
        )
        return False
    return True


@router.get("/livez", include_in_schema=False)
def livez() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz", include_in_schema=False)
def readyz(request: Request, response: Response) -> dict[str, str]:
    readiness: ReadinessCache = request.app.state.readiness
    if readiness.is_ready():
        return {"status": "ready"}
    response.status_code = 503
    return {"status": "not_ready"}
