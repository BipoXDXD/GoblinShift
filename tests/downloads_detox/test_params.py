"""Parâmetros do Downloads Detox (docs/GoblinShiftPlan.md, 1.4; ambiguidades A13 e A18)."""

import pytest

from goblinshift.processing.downloads_detox import (
    GroupBy,
    InvalidParametersError,
    NameMode,
    Params,
    params_from_mapping,
)


def test_defaults_sao_os_de_1_4() -> None:
    params = Params()

    assert params == Params(
        deduplicate=True,
        group_by=GroupBy.KIND,
        name_mode=NameMode.PRESERVE,
        name_prefix="file",
    )


@pytest.mark.parametrize(
    "prefix",
    [
        pytest.param("a", id="um-caractere"),
        pytest.param("x" * 40, id="quarenta-caracteres"),
        pytest.param("é" * 40, id="quarenta-multibyte"),
        pytest.param("foto do celular", id="com-espaco"),
        pytest.param("a.b", id="ponto-no-meio"),
    ],
)
def test_prefixo_valido_e_aceito(prefix: str) -> None:
    assert Params(name_prefix=prefix).name_prefix == prefix


@pytest.mark.parametrize(
    ("prefix", "reason"),
    [
        pytest.param("", "vazio", id="vazio"),
        pytest.param("x" * 41, "40", id="quarenta-e-um"),
        pytest.param("a/b", "separador", id="barra"),
        pytest.param("a\\b", "separador", id="contrabarra"),
        pytest.param("a\x00b", "controle", id="nul"),
        pytest.param("a\nb", "controle", id="quebra-de-linha"),
        pytest.param("a\x7fb", "controle", id="del"),
        pytest.param("a\u202eb", "controle", id="rlo"),
        pytest.param("a\u200bb", "controle", id="zwsp"),
        pytest.param("a\ufeffb", "controle", id="bom-no-meio"),
        pytest.param("a\u2066b", "controle", id="isolate"),
        pytest.param(".oculto", "ponto", id="ponto-inicial"),
        pytest.param("..", "ponto", id="ponto-ponto"),
    ],
)
def test_prefixo_invalido_e_recusado(prefix: str, reason: str) -> None:
    with pytest.raises(InvalidParametersError, match=reason):
        Params(name_prefix=prefix)


def test_mapeamento_completo_vira_params() -> None:
    raw = {"deduplicate": False, "group_by": "none", "name_mode": "indexed", "name_prefix": "doc"}

    assert params_from_mapping(raw) == Params(
        deduplicate=False, group_by=GroupBy.NONE, name_mode=NameMode.INDEXED, name_prefix="doc"
    )


def test_mapeamento_vazio_usa_os_defaults() -> None:
    assert params_from_mapping({}) == Params()


def test_parametros_resolvidos_com_preserve_e_prefixo_default_sao_aceitos() -> None:
    # O catálogo grava os defaults nos parâmetros resolvidos; o executor recebe essa forma.
    raw = {"deduplicate": True, "group_by": "kind", "name_mode": "preserve", "name_prefix": "file"}

    assert params_from_mapping(raw) == Params()


def test_campo_desconhecido_e_recusado() -> None:
    with pytest.raises(InvalidParametersError, match="desconhecid"):
        params_from_mapping({"dedup": True})


@pytest.mark.parametrize(
    ("raw", "option"),
    [
        pytest.param({"group_by": "type"}, "group_by", id="group_by-invalido"),
        pytest.param({"name_mode": "random"}, "name_mode", id="name_mode-invalido"),
        pytest.param({"group_by": 1}, "group_by", id="group_by-numero"),
        pytest.param({"deduplicate": "true"}, "deduplicate", id="booleano-como-texto"),
        pytest.param({"deduplicate": 1}, "deduplicate", id="booleano-como-numero"),
        pytest.param({"name_prefix": 7}, "name_prefix", id="prefixo-numero"),
    ],
)
def test_tipo_ou_valor_errado_e_recusado(raw: dict[str, object], option: str) -> None:
    with pytest.raises(InvalidParametersError, match=option):
        params_from_mapping(raw)
