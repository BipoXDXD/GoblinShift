"""Fábricas de recursos com dono, uma por `resource_param` da matriz de autorização.

`test_authz_owner_isolation.py` exige uma fábrica para cada parâmetro de recurso: rota nova com
recurso, sem fábrica aqui, reprova a suíte em vez de ficar sem teste de isolamento entre contas.
Uma rota cujo caminho destrutivo pede outro estado do recurso ganha fábrica própria em
`ROUTE_RESOURCES`; as demais usam a do parâmetro.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from fastapi import FastAPI
from sqlalchemy import Table, insert, select
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.files.tables import artifact, upload
from goblinshift.files.uploads import create_intents
from goblinshift.identity.tables import api_key
from goblinshift.platform.db import unit_of_work
from goblinshift.presets import create_preset, parse_draft
from goblinshift.presets.tables import preset
from goblinshift.runs.tables import run, run_output
from tests.api.files_support import OCTET_STREAM, publish, storage_of
from tests.identity.support import new_key
from tests.runs.support import insert_finished_run
from tests.runs.worker_support import queue_run

API = "/api/v1"
_UPLOAD_CONTENT = b"a,b"
# Revisão de um preset recém-criado: o pior caso, B conhece o ETag atual do preset de A.
_FIRST_REVISION = '"1"'


@dataclass(frozen=True)
class OwnedResource:
    # Cria o recurso da conta dona e devolve o id que vai no caminho.
    create: Callable[[FastAPI, UUID], UUID]
    # Estado do recurso que deve ficar intacto depois do pedido da outra conta (None: não existe).
    snapshot: Callable[[Session, UUID], object]


def _row(table: Table) -> Callable[[Session, UUID], object]:
    def snapshot(session: Session, resource_id: UUID) -> object:
        row = session.execute(select(table).where(table.c.id == resource_id)).one_or_none()
        return None if row is None else dict(row._mapping)

    return snapshot


def _factory(app: FastAPI) -> sessionmaker[Session]:
    factory: sessionmaker[Session] = app.state.session_factory
    return factory


def _api_key(app: FastAPI, owner: UUID) -> UUID:
    return new_key(_factory(app), owner).id


def _pending_upload(app: FastAPI, owner: UUID) -> UUID:
    # Pendente: aceita conteúdo, então o PUT de outra conta seria uma escrita real.
    with unit_of_work(_factory(app)) as session:
        [intent] = create_intents(session, owner, [("dados.csv", len(_UPLOAD_CONTENT))])
    return intent.id


def _output_with_preview(app: FastAPI, owner: UUID) -> UUID:
    output = publish(app, owner, b"a,b\n1,2\n", "saida.csv")
    publish(app, owner, b"a,b\n", "previews/saida.csv", preview_of=output)
    return output


def _preset(app: FastAPI, owner: UUID) -> UUID:
    with unit_of_work(_factory(app)) as session:
        return create_preset(session, owner, parse_draft("Mensal", "csv-janitor", 1, {})).id


def _queued_run(app: FastAPI, owner: UUID) -> UUID:
    # Na fila: o cancelamento de outra conta mudaria o status.
    return queue_run(_factory(app), storage_of(app), owner)


def _finished_run_with_output(app: FastAPI, owner: UUID) -> UUID:
    # Terminada e com saída: a exclusão das saídas pelo dono roda de fato (na fila seria 409).
    factory = _factory(app)
    run_id = insert_finished_run(factory, owner, datetime.now(UTC))
    output = publish(app, owner, b"relatorio", "report.json")
    with unit_of_work(factory) as session:
        session.execute(
            insert(run_output).values(
                run_id=run_id,
                position=0,
                owner_id=owner,
                artifact_id=output,
                logical_name="report.json",
            )
        )
    return run_id


def _run_and_outputs(session: Session, run_id: UUID) -> object:
    outputs = session.execute(
        select(artifact)
        .join(run_output, run_output.c.artifact_id == artifact.c.id)
        .where(run_output.c.run_id == run_id)
        .order_by(run_output.c.position)
    ).all()
    return _row(run)(session, run_id), [dict(row._mapping) for row in outputs]


OWNED_RESOURCES: dict[str, OwnedResource] = {
    "key_id": OwnedResource(_api_key, _row(api_key)),
    "upload_id": OwnedResource(_pending_upload, _row(upload)),
    "artifact_id": OwnedResource(_output_with_preview, _row(artifact)),
    "preset_id": OwnedResource(_preset, _row(preset)),
    "run_id": OwnedResource(_queued_run, _row(run)),
}

ROUTE_RESOURCES: dict[tuple[str, str], OwnedResource] = {
    ("DELETE", f"{API}/runs/{{run_id}}/artifacts"): OwnedResource(
        _finished_run_with_output, _run_and_outputs
    ),
}


def owned_resource(method: str, path: str, resource_param: str) -> OwnedResource:
    return ROUTE_RESOURCES.get((method, path)) or OWNED_RESOURCES[resource_param]


def _preset_replacement() -> dict[str, Any]:
    body = {"name": "Trocado", "recipe_id": "csv-janitor", "recipe_version": 1, "parameters": {}}
    return {"json": body, "headers": {"If-Match": _FIRST_REVISION}}


def _run_from_preset() -> dict[str, Any]:
    # Chave nova a cada pedido: um pedido não reaproveita a chave de outro.
    return {
        "json": {"input_upload_ids": [str(uuid4())]},
        "headers": {"Idempotency-Key": str(uuid4())},
    }


# Corpo e headers que tornam válido o pedido da rota, para que só o dono decida a resposta.
ROUTE_REQUESTS: dict[tuple[str, str], Callable[[], dict[str, Any]]] = {
    ("PUT", f"{API}/uploads/{{upload_id}}/content"): lambda: {
        "content": _UPLOAD_CONTENT,
        "headers": OCTET_STREAM,
    },
    ("PUT", f"{API}/presets/{{preset_id}}"): _preset_replacement,
    ("DELETE", f"{API}/presets/{{preset_id}}"): lambda: {"headers": {"If-Match": _FIRST_REVISION}},
    ("POST", f"{API}/presets/{{preset_id}}/runs"): _run_from_preset,
}
