"""Imagens da vitrine de portfólio (`docs/img/vitrine/`), geradas por `cd web && npm run vitrine`.

O CI não roda a captura (Playwright, manual e pesada); confere só o que ficou no repositório: o
manifesto bate com a pasta, declara a pilha local e um commit, cada imagem cabe no orçamento, cada
receita do catálogo tem a sua imagem e nenhuma mostra Jade nem Fagulha (D3 a: as duas ficam fora da
concessão CC BY 4.0, `docs/art/LICENSE-ART.md`).
"""

import json
import re
from pathlib import Path
from typing import Any

import pytest

from goblinshift.catalog import CATALOG

ROOT = Path(__file__).resolve().parent.parent
VITRINE = ROOT / "docs" / "img" / "vitrine"
MANIFEST = VITRINE / "vitrine.json"
PNG_MAX, GIF_MAX, DIR_MAX = 350 * 1024, 3 * 1024 * 1024, 6 * 1024 * 1024
PNG_MAX_WIDTH, PNG_MAX_HEIGHT, GIF_WIDTH = 1280, 1600, 960
RESTRICTED = {"jade", "fagulha"}  # D3 (a)
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def manifest() -> dict[str, Any]:
    if not MANIFEST.is_file():
        pytest.fail(f"{MANIFEST.relative_to(ROOT)} não existe: rode `cd web && npm run vitrine`")
    data: dict[str, Any] = json.loads(MANIFEST.read_text(encoding="utf-8"))
    return data


def image_names() -> list[str]:
    if not MANIFEST.is_file():
        return []
    return [
        entry["arquivo"] for entry in json.loads(MANIFEST.read_text(encoding="utf-8"))["imagens"]
    ]


def test_manifesto_e_pasta_batem() -> None:
    files = {p.name for p in VITRINE.iterdir()} - {"vitrine.json"}
    assert files == {e["arquivo"] for e in manifest()["imagens"]}


def test_manifesto_declara_a_pilha_local() -> None:
    data = manifest()
    assert data["pilha"] == "build local + API falsa (web/e2e/support/fakeApi.ts)"
    assert re.fullmatch(r"[0-9a-f]{40}", data["commit"])


@pytest.mark.parametrize("name", image_names())
def test_imagem_dentro_do_orcamento(name: str) -> None:
    data = (VITRINE / name).read_bytes()
    if name.endswith(".png"):
        assert len(data) <= PNG_MAX
        assert data[:8] == PNG_SIGNATURE
        width = int.from_bytes(data[16:20], "big")
        height = int.from_bytes(data[20:24], "big")
        assert width <= PNG_MAX_WIDTH
        assert height <= PNG_MAX_HEIGHT
    elif name.endswith(".gif"):
        assert len(data) <= GIF_MAX
        assert data[:6] == b"GIF89a"
        assert int.from_bytes(data[6:8], "little") == GIF_WIDTH
    else:
        pytest.fail(f"formato inesperado na vitrine: {name}")


def test_vitrine_inteira_dentro_do_orcamento() -> None:
    assert sum(p.stat().st_size for p in VITRINE.iterdir()) <= DIR_MAX


def test_nenhuma_imagem_mostra_goblin_restrita() -> None:
    assert [e["arquivo"] for e in manifest()["imagens"] if RESTRICTED & set(e["goblins"])] == []


def test_cada_receita_do_catalogo_tem_imagem() -> None:
    ids = {v.recipe_id for v in CATALOG.list_versions()}
    assert {f"receita-{i}.png" for i in ids} <= {e["arquivo"] for e in manifest()["imagens"]}
