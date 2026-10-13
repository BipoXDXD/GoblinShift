"""Sinais de vida e de prontidão (docs/GoblinShiftPlan.md, 3.8).

`/livez` só diz que o processo responde. `/readyz` depende apenas do banco, coerente com
3.5: sem banco não há como aceitar, consultar nem baixar nada. As respostas não expõem
detalhes internos, porque o monitor de uptime externo também as consulta; o motivo de
um "not_ready" vai para o log.
"""

import logging

from fastapi import APIRouter, Request, Response
from sqlalchemy.exc import SQLAlchemyError

from goblinshift.platform.schema import read_db_heads, schema_is_ready

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/livez", include_in_schema=False)
def livez() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz", include_in_schema=False)
def readyz(request: Request, response: Response) -> dict[str, str]:
    state = request.app.state
    try:
        with state.engine.connect() as conn:
            db_heads = read_db_heads(conn)
    except SQLAlchemyError:
        logger.warning("readyz: banco indisponível", exc_info=True)
        response.status_code = 503
        return {"status": "not_ready"}

    if not schema_is_ready(db_heads, state.code_heads, state.known_revisions):
        logger.warning(
            "readyz: schema incompatível (banco=%s, código=%s)",
            sorted(db_heads),
            sorted(state.code_heads),
        )
        response.status_code = 503
        return {"status": "not_ready"}

    return {"status": "ready"}
