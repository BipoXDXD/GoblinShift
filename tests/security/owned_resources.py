"""Fábricas de recursos com dono, uma por `resource_param` da matriz de autorização.

`test_authz_owner_isolation.py` exige uma fábrica para cada parâmetro de recurso: rota nova com
recurso, sem fábrica aqui, reprova a suíte em vez de ficar sem teste de isolamento entre contas.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from fastapi import FastAPI
from sqlalchemy import Table
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.files.tables import artifact, upload
from goblinshift.files.uploads import create_intents
from goblinshift.identity.tables import api_key
from goblinshift.platform.db import unit_of_work
from goblinshift.presets import create_preset, parse_draft
from goblinshift.presets.tables import preset
from goblinshift.runs.tables import run
from tests.api.files_support import OCTET_STREAM, publish, storage_of
from tests.identity.support import new_key
from tests.runs.worker_support import queue_run

API = "/api/v1"
_UPLOAD_CONTENT = b"a,b"
# Revisão de um preset recém-criado: o pior caso, B conhece o ETag atual do preset de A.
_FIRST_REVISION = '"1"'


@dataclass(frozen=True)
class OwnedResource:
    # Cria o recurso da conta dona e devolve o id que vai no caminho.
    create: Callable[[FastAPI, UUID], UUID]
    # Tabela cuja linha do recurso deve ficar intacta depois do pedido da outra conta.
    table: Table


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


OWNED_RESOURCES: dict[str, OwnedResource] = {
    "key_id": OwnedResource(_api_key, api_key),
    "upload_id": OwnedResource(_pending_upload, upload),
    "artifact_id": OwnedResource(_output_with_preview, artifact),
    "preset_id": OwnedResource(_preset, preset),
    "run_id": OwnedResource(_queued_run, run),
}

_PRESET_BODY = {
    "name": "Trocado",
    "recipe_id": "csv-janitor",
    "recipe_version": 1,
    "parameters": {},
}

# Corpo e headers que tornam válido o pedido da rota, para que só o dono decida a resposta.
ROUTE_REQUESTS: dict[tuple[str, str], dict[str, Any]] = {
    ("PUT", f"{API}/uploads/{{upload_id}}/content"): {
        "content": _UPLOAD_CONTENT,
        "headers": OCTET_STREAM,
    },
    ("PUT", f"{API}/presets/{{preset_id}}"): {
        "json": _PRESET_BODY,
        "headers": {"If-Match": _FIRST_REVISION},
    },
    ("DELETE", f"{API}/presets/{{preset_id}}"): {"headers": {"If-Match": _FIRST_REVISION}},
    ("POST", f"{API}/presets/{{preset_id}}/runs"): {
        "json": {"input_upload_ids": [str(uuid4())]},
        "headers": {"Idempotency-Key": str(uuid4())},
    },
}
