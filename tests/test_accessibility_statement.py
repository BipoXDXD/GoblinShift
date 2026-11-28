"""A declaração de acessibilidade acompanha o CI (Tarefa 16 do plano de paridade).

`docs/acessibilidade.md` cita as specs que medem cada critério. Os testes amarram o texto ao
código: spec renomeada ou removida, spec de acessibilidade nova sem citação ou tag do axe trocada
reprovam a suíte, em vez de deixar a declaração prometer o que o CI não mede.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATEMENT = ROOT / "docs" / "acessibilidade.md"
# As tags do axe ficam no apoio que o contrast.spec.ts usa (`expectNoWcagViolations`).
AXE_SUPPORT = ROOT / "web" / "e2e" / "support" / "axe.ts"
SPEC_PATH = re.compile(r"web/e2e/specs/[\w-]+\.spec\.ts")
# As specs do CI que medem acessibilidade; nova spec do tipo entra aqui e na declaração.
A11Y_SPECS = {"contrast", "focus", "reflow", "text-spacing", "motion", "controls", "layout-shift"}


def statement() -> str:
    return STATEMENT.read_text(encoding="utf-8")


def cited_specs(text: str) -> set[str]:
    """Os caminhos `web/e2e/specs/*.spec.ts` citados no texto."""
    return set(SPEC_PATH.findall(text))


def wcag_tags_of(path: Path) -> set[str]:
    """As tags do axe (`wcag2a`, `wcag22aa`…) escritas como string no arquivo."""
    return set(re.findall(r"[\"'](wcag2\w+)[\"']", path.read_text(encoding="utf-8")))


def test_toda_spec_citada_existe() -> None:
    assert {path for path in cited_specs(statement()) if not (ROOT / path).exists()} == set()


def test_toda_spec_de_acessibilidade_do_ci_esta_citada() -> None:
    expected = {f"web/e2e/specs/{name}.spec.ts" for name in A11Y_SPECS}

    assert expected - cited_specs(statement()) == set()


def test_tags_do_axe_iguais_as_do_ci() -> None:
    tags = wcag_tags_of(AXE_SUPPORT)

    assert tags, "nenhuma tag do axe encontrada no apoio do contrast.spec.ts"
    assert set(re.findall(r"`(wcag2\w+)`", statement())) == tags


def test_declara_alvo_e_estado() -> None:
    text = statement()

    assert "WCAG 2.2" in text
    assert "parcialmente conforme" in text


def test_tem_contato_e_data_da_revisao() -> None:
    text = statement()

    assert "contato@goblinshift.dev" in text
    assert re.search(r"\*\*Última revisão:\*\* \d{4}-\d{2}-\d{2}", text)
