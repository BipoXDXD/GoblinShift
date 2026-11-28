"""O README como vitrine de portfólio: cinco receitas, imagens da vitrine e links que existem.

Lê o `CATALOG` para que uma receita nova sem linha nem imagem no README reprove, e confere que
todo link relativo e toda âncora do plano citados no README apontam para algo que existe.
"""

import re
from pathlib import Path

import pytest

from goblinshift.catalog import CATALOG
from tests.test_vitrine_imagens import RESTRICTED, vitrine_files

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")
PLAN_TEXT = (ROOT / "docs" / "GoblinShiftPlan.md").read_text(encoding="utf-8")

NAMES = {
    "csv-janitor": "CSV Janitor",
    "image-forge": "Image Forge",
    "downloads-detox": "Downloads Detox",
    "pdf-press": "PDF Press",
    "media-mill": "Media Mill",
}
LINK = re.compile(r"\]\(([^)\s]+)\)|<img [^>]*src=\"([^\"]+)\"")
PLAN_ANCHOR = re.compile(r"docs/GoblinShiftPlan\.md#([\w-]+)")


def relative_targets(text: str) -> list[str]:
    """Alvos de link e de `<img>` que apontam para arquivos do repositório."""
    targets = {a or b for a, b in LINK.findall(text)}
    external = ("http://", "https://", "mailto:", "#")
    return sorted(t for t in targets if not t.startswith(external))


def plan_anchors(text: str) -> list[str]:
    return sorted(set(PLAN_ANCHOR.findall(text)))


def showcase() -> str:
    """A parte de vitrine: do início até a tabela "Documentos"."""
    return README.split("\n## Documentos\n", 1)[0]


def test_nomes_cobrem_o_catalogo() -> None:
    assert set(NAMES) == {v.recipe_id for v in CATALOG.list_versions()}


def test_abertura_nomeia_as_cinco_receitas() -> None:
    opening = README.split("\n## ", 1)[0]
    assert [n for n in NAMES.values() if n not in opening] == []


def test_readme_tem_links_e_ancoras_do_plano() -> None:
    """Guarda dos dois testes parametrizados: com a lista vazia, o pytest os pula sem rodar."""
    assert relative_targets(README)
    assert plan_anchors(README)


@pytest.mark.parametrize("target", relative_targets(README))
def test_links_relativos_do_readme_existem(target: str) -> None:
    assert relative_targets(README)  # regex quebrado reprova em vez de pular o teste
    assert (ROOT / target.split("#", 1)[0]).exists(), target


@pytest.mark.parametrize("anchor", plan_anchors(README))
def test_ancoras_do_plano_existem(anchor: str) -> None:
    assert plan_anchors(README)  # regex quebrado reprova em vez de pular o teste
    assert f'<a id="{anchor}"></a>' in PLAN_TEXT


def test_readme_cita_cada_receita_com_imagem() -> None:
    for recipe_id in NAMES:
        assert f"| `{recipe_id}` |" in README, recipe_id
        assert f"docs/img/vitrine/receita-{recipe_id}.png" in README, recipe_id


def test_toda_imagem_da_vitrine_aparece_no_readme() -> None:
    missing = [
        p.name
        for p in vitrine_files()
        if p.suffix in {".png", ".gif"} and f"docs/img/vitrine/{p.name}" not in README
    ]
    assert missing == []


def test_readme_sem_aviso_de_em_construcao() -> None:
    assert "Em construção" not in README and "quatro receitas" not in README


def test_secoes_da_vitrine() -> None:
    for heading in ("## As cinco receitas", "## Arquitetura", "## Segurança", "## Testes e CI"):
        assert f"\n{heading}\n" in README, heading


def test_vitrine_sem_goblin_restrita() -> None:
    text = showcase().lower()
    assert sorted(name for name in RESTRICTED if name in text) == []
