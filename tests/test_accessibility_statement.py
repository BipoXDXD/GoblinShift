"""A declaração de acessibilidade acompanha o CI (Tarefa 16 do plano de paridade).

`docs/acessibilidade.md` cita as specs que medem cada critério. Os testes amarram o texto ao
código: spec citada renomeada ou removida, spec da lista `A11Y_SPECS` sem citação, axe que o
`contrast.spec.ts` deixou de chamar ou tag do axe trocada reprovam a suíte, em vez de deixar a
declaração prometer o que o CI não mede.

A lista `A11Y_SPECS` é mantida à mão: o nome do arquivo não diz se a spec mede acessibilidade
(várias outras citam a WCAG de passagem). Spec de acessibilidade nova só entra na declaração se
alguém a puser na lista.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATEMENT = ROOT / "docs" / "acessibilidade.md"
# As tags do axe ficam no apoio que o contrast.spec.ts usa (`expectNoWcagViolations`).
AXE_SUPPORT = ROOT / "web" / "e2e" / "support" / "axe.ts"
SPEC_PATH = re.compile(r"web/e2e/specs/[\w-]+\.spec\.ts")
CONTRAST_SPEC = ROOT / "web" / "e2e" / "specs" / "contrast.spec.ts"
# As specs do CI que medem acessibilidade, mantidas à mão (ver a docstring do módulo).
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


def test_contrast_spec_mede_com_o_axe_das_tags_declaradas() -> None:
    """A declaração diz que o `contrast.spec.ts` roda o axe com as tags: ele precisa chamar o
    apoio, e o apoio precisa passar as tags ao axe."""
    spec = CONTRAST_SPEC.read_text(encoding="utf-8")
    support = AXE_SUPPORT.read_text(encoding="utf-8")

    assert 'import { expectNoWcagViolations } from "../support/axe";' in spec
    assert re.search(r"await expectNoWcagViolations\(page,", spec)
    assert ".withTags(WCAG_22_AA)" in support


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
