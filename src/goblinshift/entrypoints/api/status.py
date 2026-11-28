"""`GET /api/v1/status`: estado do serviço sem login (D9 do plano de paridade; 3.7).

Pública de propósito: a página `/estado` serve justamente quando o login falha. Só níveis e a
hora da leitura, sem nenhuma contagem (`runs.service_status`). Com o banco fora, segue o `503`
com `Retry-After: 5` dos handlers comuns, que a página mostra como "API fora do ar". Ela não cobre
a VPS inteira fora do ar: roda no mesmo servidor.
"""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Response
from pydantic import BaseModel

from goblinshift.entrypoints.api.dependencies import DbSession
from goblinshift.runs.service_status import ProcessingLevel, QueueLevel, read_service_status

router = APIRouter()


class ServiceStatusResponse(BaseModel):
    # Quem recebe a resposta sabe que a API está de pé; fora do ar, a resposta é o 503.
    api: Literal["up"]
    processing: ProcessingLevel
    queue: QueueLevel
    checked_at: datetime


@router.get("/status", operation_id="getServiceStatus")
def get_service_status(session: DbSession, response: Response) -> ServiceStatusResponse:
    with session.begin():
        status = read_service_status(session)
    response.headers["Cache-Control"] = "no-store"
    return ServiceStatusResponse(
        api="up",
        processing=status.processing,
        queue=status.queue,
        checked_at=status.checked_at,
    )
