"""O contrato versionado em web/openapi/openapi.json acompanha a API (fatia 7, F1)."""

import json
from pathlib import Path

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
