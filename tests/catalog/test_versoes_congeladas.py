"""Versões publicadas congeladas: schema e defaults não mudam dentro da versão (1.6).

Cada versão do catálogo tem um snapshot em `congeladas/<recipe_id>-v<versão>.json`, gravado por
`scripts/congelar_versao.py`. Versão nova acrescenta o seu no mesmo PR; um existente nunca se
altera. Mudou o schema ou um default? É uma versão nova, não um snapshot novo.
"""

import json
from pathlib import Path

import pytest

from goblinshift.catalog import CATALOG, RecipeVersion
from goblinshift.catalog.versions import frozen_view
from scripts import congelar_versao

FROZEN = Path(__file__).parent / "congeladas"


def _key(version: RecipeVersion) -> str:
    return f"{version.recipe_id}-{version.version}"


def _snapshot(version: RecipeVersion) -> Path:
    return FROZEN / f"{version.recipe_id}-v{version.version}.json"


@pytest.mark.parametrize("version", CATALOG.list_versions(), ids=_key)
def test_versao_publicada_nao_muda(version: RecipeVersion) -> None:
    path = _snapshot(version)
    assert path.is_file(), f"rode scripts/congelar_versao.py {version.recipe_id} {version.version}"
    assert json.loads(path.read_text("utf-8")) == json.loads(json.dumps(frozen_view(version)))


@pytest.mark.parametrize("version", CATALOG.list_versions(), ids=_key)
def test_versao_publicada_nao_muda_schema_nem_defaults(version: RecipeVersion) -> None:
    # Confere direto contra o modelo da versão, sem passar por `frozen_view`: mudar a função não
    # afrouxa a rede.
    path = _snapshot(version)
    assert path.is_file(), f"{path.name} ausente: toda versão publicada congela schema e defaults"
    frozen = json.loads(path.read_text(encoding="utf-8"))
    assert version.variant_schema() == frozen["variant_schema"], (
        "schema de versão publicada mudou: crie uma versão nova (GoblinShiftPlan 1.6)"
    )
    assert version.parameters_schema() == frozen["variant_schema"]["properties"]["parameters"]
    assert version.resolve({}) == frozen["defaults"], (
        "default de versão publicada mudou: crie uma versão nova (GoblinShiftPlan 1.6)"
    )


def test_todo_snapshot_e_de_uma_versao_do_catalogo() -> None:
    # Versão retirada continua no catálogo; sumir dele apagaria a rede em silêncio.
    published = {_snapshot(version).name for version in CATALOG.list_versions()}
    orphans = sorted(path.name for path in FROZEN.glob("*.json") if path.name not in published)
    assert not orphans, f"snapshot sem versão no catálogo: {orphans}"


def test_script_grava_snapshot_no_formato_canonico(tmp_path: Path) -> None:
    version = CATALOG.get_version("image-forge", 1)

    assert congelar_versao.main(["image-forge", "1"], destino=tmp_path) == 0

    expected = json.dumps(frozen_view(version), indent=2, sort_keys=True, ensure_ascii=False)
    assert (tmp_path / "image-forge-v1.json").read_text("utf-8") == expected + "\n"


def test_script_recusa_sobrescrever_snapshot(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    existing = tmp_path / "csv-janitor-v1.json"
    existing.write_text("X", encoding="utf-8")

    assert congelar_versao.main(["csv-janitor", "1"], destino=tmp_path) == 1

    assert existing.read_text("utf-8") == "X"
    assert "snapshot congelado não se sobrescreve" in capsys.readouterr().err


def test_script_recusa_versao_fora_do_catalogo(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert congelar_versao.main(["csv-janitor", "99"], destino=tmp_path) == 1

    assert not list(tmp_path.iterdir())
    assert "csv-janitor v99" in capsys.readouterr().err
