"""Modelo Pydantic dos parâmetros do downloads-detox v1 (1.4, 1.6; ambiguidades A13 e A18)."""

import pytest
from pydantic import ValidationError

from goblinshift.catalog import CATALOG
from goblinshift.catalog.downloads_detox_v1 import DownloadsDetoxV1Parameters
from goblinshift.catalog.downloads_detox_v2 import DownloadsDetoxV2Parameters
from goblinshift.catalog.versions import RecipeVersion, VersionStatus
from goblinshift.processing.downloads_detox import NameMode, Params, params_from_mapping
from goblinshift.processing.downloads_detox.recipe import REJECTION_CODE
from goblinshift.processing.recipe import ItemOk, ItemRejected
from tests.downloads_detox import samples
from tests.downloads_detox.conftest import Workspace


def version() -> RecipeVersion:
    return RecipeVersion(
        recipe_id="downloads-detox",
        version=1,
        status=VersionStatus.CURRENT,
        parameters_model=DownloadsDetoxV1Parameters,
        limits={},
    )


def errors(raw: object) -> list[tuple[str, ...]]:
    with pytest.raises(ValidationError) as caught:
        DownloadsDetoxV1Parameters.model_validate(raw)
    return [tuple(str(part) for part in error["loc"]) for error in caught.value.errors()]


def test_corpo_vazio_resolve_para_os_defaults_em_forma_canonica() -> None:
    assert version().resolve({}) == {
        "deduplicate": True,
        "group_by": "kind",
        "name_mode": "preserve",
        "name_prefix": "file",
    }


def test_resolvidos_viram_os_params_da_biblioteca() -> None:
    resolved = version().resolve({"name_mode": "indexed", "name_prefix": "doc"})

    assert params_from_mapping(resolved) == Params(name_mode=NameMode.INDEXED, name_prefix="doc")


def test_prefixo_explicito_com_indexed_e_aceito() -> None:
    model = DownloadsDetoxV1Parameters.model_validate({"name_mode": "indexed", "name_prefix": "a"})

    assert model.name_prefix == "a"


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param({"name_prefix": "doc"}, id="preserve-por-default"),
        pytest.param({"name_mode": "preserve", "name_prefix": "doc"}, id="preserve-explicito"),
    ],
)
def test_prefixo_explicito_com_preserve_e_recusado(raw: dict[str, object]) -> None:
    assert errors(raw) == [()]


def test_prefixo_igual_ao_default_com_preserve_e_aceito() -> None:
    # A13 (decisão de 2026-10-16): o default volta nos parâmetros resolvidos e pode ser reenviado.
    model = DownloadsDetoxV1Parameters.model_validate(
        {"name_mode": "preserve", "name_prefix": "file"}
    )

    assert model.name_prefix == "file"


@pytest.mark.parametrize(
    ("raw", "location"),
    [
        pytest.param({"deduplicate": "true"}, ("deduplicate",), id="booleano-como-texto"),
        pytest.param({"group_by": "type"}, ("group_by",), id="group_by-invalido"),
        pytest.param({"name_mode": "random"}, ("name_mode",), id="name_mode-invalido"),
        pytest.param({"extra": 1}, ("extra",), id="campo-desconhecido"),
        pytest.param({"name_mode": "indexed", "name_prefix": ""}, ("name_prefix",), id="vazio"),
        pytest.param(
            {"name_mode": "indexed", "name_prefix": "x" * 41}, ("name_prefix",), id="41-chars"
        ),
        pytest.param(
            {"name_mode": "indexed", "name_prefix": "a/b"}, ("name_prefix",), id="separador"
        ),
        pytest.param(
            {"name_mode": "indexed", "name_prefix": "a\tb"}, ("name_prefix",), id="controle"
        ),
        pytest.param(
            {"name_mode": "indexed", "name_prefix": ".a"}, ("name_prefix",), id="ponto-inicial"
        ),
    ],
)
def test_valor_invalido_aponta_o_campo(raw: dict[str, object], location: tuple[str, ...]) -> None:
    assert errors(raw) == [location]


def test_prefixo_de_quarenta_caracteres_e_aceito() -> None:
    raw = {"name_mode": "indexed", "name_prefix": "x" * 40}

    assert DownloadsDetoxV1Parameters.model_validate(raw).name_prefix == "x" * 40


def test_schema_publica_limites_do_prefixo() -> None:
    schema = version().parameters_schema()

    prefix = schema["properties"]["name_prefix"]
    assert (prefix["minLength"], prefix["maxLength"], prefix["default"]) == (1, 40, "file")
    assert schema["additionalProperties"] is False


# --- Versão 2: o mesmo schema, com semântica nova (formatos aceitos) --------------------------


def test_registro_da_detox_v2_aceita_pdf(work: Workspace) -> None:
    work.add("a.pdf", samples.pdf())

    report = work.run_registered(2, {})

    [item] = report.items
    assert isinstance(item, ItemOk)
    assert item.outputs == ("documents/a.pdf",)


def test_registro_da_detox_v1_continua_recusando_pdf(work: Workspace) -> None:
    work.add("a.pdf", samples.pdf())

    report = work.run_registered(1, {})

    [item] = report.items
    assert isinstance(item, ItemRejected)
    assert item.code == REJECTION_CODE == "UNSUPPORTED_FORMAT"


def test_modelo_da_v2_tem_o_schema_da_v1_com_outro_titulo() -> None:
    v1 = DownloadsDetoxV1Parameters.model_json_schema()
    v2 = DownloadsDetoxV2Parameters.model_json_schema()

    assert v2.pop("title") == "DownloadsDetoxV2Parameters"
    assert v1.pop("title") == "DownloadsDetoxV1Parameters"
    assert v2 == v1


def test_v2_recusa_com_preserve_outro_name_prefix() -> None:
    with pytest.raises(ValidationError):
        DownloadsDetoxV2Parameters.model_validate({"name_prefix": "doc"})


def test_detox_v1_deprecated_e_v2_atual_com_os_mesmos_defaults() -> None:
    assert CATALOG.resolve("downloads-detox", 2, {}) == CATALOG.resolve("downloads-detox", 1, {})
    assert CATALOG.get_version("downloads-detox", 1).status is VersionStatus.DEPRECATED
    assert CATALOG.get_version("downloads-detox", 2).status is VersionStatus.CURRENT
