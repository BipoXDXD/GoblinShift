"""`GET /api/v1/catalog`: receitas, versões aceitas, limites, defaults e schema (1.6)."""

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from goblinshift.catalog import CATALOG, Catalog, VersionStatus
from goblinshift.entrypoints.api.catalog import catalog_response
from goblinshift.identity import Scope
from tests.api.support import assert_problem
from tests.identity.support import new_key, new_user

CATALOG_PATH = "/api/v1/catalog"
VERSION_KEYS = {"version", "status", "limits", "defaults", "parameters_schema"}


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def version_of(body: dict[str, object], recipe_id: str) -> dict[str, object]:
    recipes = body["recipes"]
    assert isinstance(recipes, list)
    [recipe] = [recipe for recipe in recipes if recipe["recipe_id"] == recipe_id]
    [version] = recipe["versions"]
    assert isinstance(version, dict)
    return version


# Sem banco: a montagem da resposta é uma função pura do catálogo.


def test_lista_as_tres_receitas_na_ordem_do_catalogo() -> None:
    body = catalog_response(CATALOG).model_dump(mode="json")

    assert [recipe["recipe_id"] for recipe in body["recipes"]] == [
        "csv-janitor",
        "image-forge",
        "downloads-detox",
    ]


def test_cada_nivel_traz_exatamente_as_chaves_do_contrato() -> None:
    body = catalog_response(CATALOG).model_dump(mode="json")

    assert body.keys() == {"recipes"}
    for recipe in body["recipes"]:
        assert recipe.keys() == {"recipe_id", "versions"}
        for version in recipe["versions"]:
            assert version.keys() == VERSION_KEYS


def test_versao_traz_estado_limites_e_defaults_resolvidos() -> None:
    body = catalog_response(CATALOG).model_dump(mode="json")

    assert version_of(body, "image-forge") == {
        "version": 1,
        "status": "current",
        "limits": {
            "max_inputs": 100,
            "max_pixels": 24_000_000,
            "max_axis_px": 8192,
            "thumbnail_box_px": 320,
        },
        "defaults": {
            "jpeg_background": "#FFFFFF",
            "jpeg_quality": 85,
            "make_thumbnails": False,
            "max_height": 1600,
            "max_width": 1600,
            "output_format": "preserve",
        },
        "parameters_schema": version_of(body, "image-forge")["parameters_schema"],
    }


def test_limites_do_detox_incluem_o_teto_de_bytes_do_lote() -> None:
    body = catalog_response(CATALOG).model_dump(mode="json")

    assert version_of(body, "downloads-detox")["limits"] == {
        "max_inputs": 100,
        "max_total_input_bytes": 480 * 1024 * 1024,
    }


def test_schema_dos_parametros_e_o_mesmo_da_variante_de_post_runs() -> None:
    body = catalog_response(CATALOG).model_dump(mode="json")
    schema = version_of(body, "csv-janitor")["parameters_schema"]

    assert schema == CATALOG.get_version("csv-janitor", 1).parameters_schema()
    assert isinstance(schema, dict)
    assert "$defs" not in schema


def test_versao_retirada_fica_fora_e_a_deprecated_aparece_com_o_estado() -> None:
    v1 = CATALOG.get_version("csv-janitor", 1)
    catalog = Catalog(
        [
            replace(v1, status=VersionStatus.RETIRED),
            replace(v1, version=2, status=VersionStatus.DEPRECATED),
            replace(v1, version=3),
        ]
    )

    body = catalog_response(catalog).model_dump(mode="json")

    [recipe] = body["recipes"]
    assert [(v["version"], v["status"]) for v in recipe["versions"]] == [
        (2, "deprecated"),
        (3, "current"),
    ]


def test_receita_so_com_versoes_retiradas_nao_aparece() -> None:
    v1 = CATALOG.get_version("csv-janitor", 1)
    catalog = Catalog([replace(v1, status=VersionStatus.RETIRED)])

    assert catalog_response(catalog).model_dump(mode="json") == {"recipes": []}


# Pela API, com banco real.


@pytest.mark.integration
def test_qualquer_chave_valida_le_o_catalogo(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    issued = new_key(factory, new_user(factory), scopes=frozenset({Scope.FILES_READ}))

    response = client.get(CATALOG_PATH, headers=bearer(issued.token))

    assert response.status_code == 200
    assert response.json() == catalog_response(CATALOG).model_dump(mode="json")


@pytest.mark.integration
def test_sem_credencial_responde_401_sem_o_catalogo(client: TestClient) -> None:
    response = client.get(CATALOG_PATH)

    assert_problem(response, 401, "AUTHENTICATION_REQUIRED")
    assert "image-forge" not in response.text


@pytest.mark.integration
def test_resposta_traz_etag_forte_e_exige_revalidacao(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    issued = new_key(factory, new_user(factory))

    response = client.get(CATALOG_PATH, headers=bearer(issued.token))

    etag = response.headers["etag"]
    assert etag.startswith('"')
    assert etag.endswith('"')
    assert len(etag) > 2
    assert response.headers["cache-control"] == "private, no-cache"


@pytest.mark.integration
def test_etag_e_estavel_entre_requisicoes_e_contas(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    first = new_key(factory, new_user(factory))
    second = new_key(factory, new_user(factory))

    etags = {
        client.get(CATALOG_PATH, headers=bearer(key.token)).headers["etag"]
        for key in (first, first, second)
    }

    assert len(etags) == 1


@pytest.mark.integration
@pytest.mark.parametrize(
    "if_none_match",
    [
        pytest.param("{etag}", id="igual"),
        pytest.param("W/{etag}", id="fraca"),
        pytest.param('"outra", {etag}', id="numa-lista"),
        pytest.param("*", id="asterisco"),
    ],
)
def test_if_none_match_que_casa_responde_304_sem_corpo(
    client: TestClient, factory: sessionmaker[Session], if_none_match: str
) -> None:
    issued = new_key(factory, new_user(factory))
    etag = client.get(CATALOG_PATH, headers=bearer(issued.token)).headers["etag"]

    response = client.get(
        CATALOG_PATH,
        headers={**bearer(issued.token), "If-None-Match": if_none_match.format(etag=etag)},
    )

    assert response.status_code == 304
    assert response.content == b""
    assert response.headers["etag"] == etag
    assert response.headers["cache-control"] == "private, no-cache"


@pytest.mark.integration
@pytest.mark.parametrize("if_none_match", ['"desatualizado"', "", "lixo sem aspas"])
def test_if_none_match_que_nao_casa_devolve_o_catalogo(
    client: TestClient, factory: sessionmaker[Session], if_none_match: str
) -> None:
    issued = new_key(factory, new_user(factory))

    response = client.get(
        CATALOG_PATH, headers={**bearer(issued.token), "If-None-Match": if_none_match}
    )

    assert response.status_code == 200
    assert response.json()["recipes"]


@pytest.mark.integration
def test_if_none_match_sem_credencial_continua_401(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    issued = new_key(factory, new_user(factory))
    etag = client.get(CATALOG_PATH, headers=bearer(issued.token)).headers["etag"]

    response = client.get(CATALOG_PATH, headers={"If-None-Match": etag})

    assert_problem(response, 401, "AUTHENTICATION_REQUIRED")
