"""O contrato versionado em web/openapi/openapi.json acompanha a API (fatia 7, F1)."""

import json
from pathlib import Path
from typing import Any

import pytest

from goblinshift.entrypoints.openapi_export import main, render_openapi

COMMITTED_SPEC = Path(__file__).resolve().parents[1] / "web" / "openapi" / "openapi.json"


def test_contrato_versionado_esta_atualizado() -> None:
    assert COMMITTED_SPEC.read_text(encoding="utf-8") == render_openapi(), (
        "web/openapi/openapi.json está desatualizado: rode `uv run gs-export-openapi` "
        "e, em web/, `npm run generate:api`."
    )


def test_export_nao_depende_da_versao_do_deploy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GS_SERVICE_VERSION", "sha-0123456789ab")

    spec = json.loads(render_openapi())

    assert spec["info"]["version"] != "sha-0123456789ab"


def test_export_termina_com_quebra_de_linha_e_sem_escape_de_acentos() -> None:
    text = render_openapi()

    assert text.endswith("}\n")
    assert "\\u00" not in text


def test_main_grava_o_contrato_no_caminho_pedido(tmp_path: Path) -> None:
    target = tmp_path / "openapi.json"

    exit_code = main([str(target)])

    assert exit_code == 0
    assert target.read_text(encoding="utf-8") == render_openapi()


def _operations_by_id(spec: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        operation["operationId"]: operation
        for path_item in spec["paths"].values()
        for operation in path_item.values()
    }


def test_problemas_publicam_o_schema_no_media_type_de_erro() -> None:
    operations = _operations_by_id(json.loads(render_openapi()))

    for operation_id, operation in operations.items():
        for status, response in operation["responses"].items():
            if status.startswith(("4", "5")):
                assert response["content"] == {
                    "application/problem+json": {
                        "schema": {"$ref": "#/components/schemas/ProblemDetails"}
                    }
                }, (operation_id, status)


@pytest.mark.parametrize(
    ("operation_id", "media_types"),
    [
        ("downloadArtifact", ("application/octet-stream",)),
        ("getArtifactPreview", ("image/jpeg", "application/octet-stream")),
    ],
)
def test_artefatos_publicam_o_sucesso_binario(
    operation_id: str, media_types: tuple[str, ...]
) -> None:
    operation = _operations_by_id(json.loads(render_openapi()))[operation_id]

    assert operation["responses"]["200"]["content"] == {
        media_type: {"schema": {"type": "string", "format": "binary"}} for media_type in media_types
    }


def _assert_retry_after_header(response: dict[str, Any]) -> None:
    assert "application/problem+json" in response["content"]
    assert response["headers"]["Retry-After"] == {
        "description": "Segundos até poder repetir a requisição.",
        "required": True,
        "schema": {"type": "string", "pattern": "^[0-9]+$"},
    }


def test_put_do_conteudo_do_upload_documenta_503_com_retry_after_obrigatorio() -> None:
    # Disco cheio, cota ou erro de E/S na gravação respondem 503 com `Retry-After: 60`.
    operation = _operations_by_id(json.loads(render_openapi()))["putUploadContent"]

    _assert_retry_after_header(operation["responses"]["503"])


def test_todo_503_documentado_traz_retry_after_obrigatorio() -> None:
    # Todo 503 que a API emite (banco, storage, ambiente sem espaço, fila cheia) leva o header.
    operations = _operations_by_id(json.loads(render_openapi()))

    with_503 = {
        name: op["responses"]["503"] for name, op in operations.items() if "503" in op["responses"]
    }

    assert with_503
    for response in with_503.values():
        _assert_retry_after_header(response)


def test_rotas_nao_deprecated_nao_usam_dois_pontos() -> None:
    spec = json.loads(render_openapi())

    assert [
        (method, path)
        for path, item in spec["paths"].items()
        for method, operation in item.items()
        if ":" in path and not operation.get("deprecated", False)
    ] == []


def test_aceite_dos_termos_tem_a_rota_nova_e_o_alias_deprecated() -> None:
    paths = json.loads(render_openapi())["paths"]

    assert paths["/api/v1/me/terms/accept"]["post"]["operationId"] == "acceptCurrentTerms"
    assert "deprecated" not in paths["/api/v1/me/terms/accept"]["post"]
    legacy = paths["/api/v1/me/terms:accept"]["post"]
    assert legacy["operationId"] == "acceptTerms"
    assert legacy["deprecated"] is True
