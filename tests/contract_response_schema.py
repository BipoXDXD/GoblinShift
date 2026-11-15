"""Regressão executada por scripts/contract-test.sh com o grupo `contract`."""

import json
from datetime import timedelta
from typing import Any

import httpx2
import pytest
import schemathesis
from schemathesis.core.failures import FailureGroup
from schemathesis.openapi.checks import JsonSchemaError
from schemathesis.specs.openapi.checks import response_schema_conformance

from goblinshift.entrypoints.openapi_export import render_openapi
from scripts.contract_spec import derive

ARTIFACT = "/api/v1/artifacts/{artifact_id}"
DOWNLOAD = f"{ARTIFACT}/download"
ARTIFACT_ID = "00000000-0000-4000-8000-000000000000"


@pytest.fixture(scope="module")
def contract() -> Any:
    return schemathesis.openapi.from_dict(derive(json.loads(render_openapi())))


@pytest.mark.parametrize(
    ("path", "status"),
    [
        (DOWNLOAD, 401),
        (DOWNLOAD, 403),
        (DOWNLOAD, 404),
        (DOWNLOAD, 410),
        (DOWNLOAD, 422),
        (DOWNLOAD, 429),
        (DOWNLOAD, 500),
        (DOWNLOAD, 503),
        (ARTIFACT, 404),
    ],
)
def test_schemathesis_rejeita_problema_sem_campos_obrigatorios(
    contract: Any, path: str, status: int
) -> None:
    case = contract[path]["GET"].Case(path_parameters={"artifact_id": ARTIFACT_ID})
    response = httpx2.Response(
        status,
        json={},
        headers={"Content-Type": "application/problem+json"},
        request=httpx2.Request("GET", "http://127.0.0.1" + path.format(artifact_id=ARTIFACT_ID)),
    )
    response.elapsed = timedelta(0)

    with pytest.raises(FailureGroup) as caught:
        case.validate_response(response, checks=[response_schema_conformance])

    (failure,) = caught.value.exceptions
    assert isinstance(failure, JsonSchemaError)
