"""O plano e os ADRs contam o mesmo estado da `main`.

A tabela de ADRs da seção 3.10 do plano marca como proposta (`*Em revisão`, `*Proposto`,
`*Em parte proposto`) exatamente os ADRs cujo arquivo diz "proposto" na linha do estado; o
plano não cita PR mesclado como aberto; e a seção 3.9 abre com um estado datado.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLAN_TEXT = (ROOT / "docs" / "GoblinShiftPlan.md").read_text(encoding="utf-8")
ADR_DIR = ROOT / "docs" / "adr"

_TABLE_ROW = re.compile(r"^\| (\d{3}) \| ([^|]*)\|", re.MULTILINE)
_PROPOSED_PREFIXES = ("*Em revisão", "*Proposto", "*Em parte proposto")
# Histórico mantido na linha do estado depois da aceitação ("antes: proposto em …").
_HISTORY_MARK = "antes:"


def adr_table_rows(plan_text: str) -> dict[str, str]:
    """Número do ADR (3 dígitos) → célula da decisão, na tabela da seção 3.10."""
    section = plan_text.split('<a id="s3-10"></a>')[1]
    return {number: cell.strip() for number, cell in _TABLE_ROW.findall(section)}


def is_proposed_in_table(cell: str) -> bool:
    """A célula da decisão começa com uma marca de proposta."""
    return cell.startswith(_PROPOSED_PREFIXES)


def _state_block(adr_text: str) -> str:
    """Bloco do `- **Estado:**` até o próximo item de primeiro nível; sem ele, `## Status`."""
    lines = adr_text.splitlines()
    for index, line in enumerate(lines):
        if line.startswith("- **Estado:**"):
            block = [line]
            for following in lines[index + 1 :]:
                if following.startswith("- **") or not following.strip():
                    break
                block.append(following)
            return "\n".join(block)
    status = adr_text.split("\n## Status\n", 1)
    if len(status) == 1:
        return ""
    return status[1].split("\n## ", 1)[0]


def is_proposed_in_file(adr_text: str) -> bool:
    """O estado do ADR diz "proposto", sem contar o histórico depois de "antes:"."""
    current = _state_block(adr_text).split(_HISTORY_MARK, 1)[0]
    return "proposto" in current.lower()


@pytest.mark.parametrize("number", sorted(adr_table_rows(PLAN_TEXT)))
def test_estado_do_adr_na_tabela_do_plano_bate_com_o_arquivo(number: str) -> None:
    adr = next(ADR_DIR.glob(f"{number}-*.md")).read_text(encoding="utf-8")
    assert is_proposed_in_table(adr_table_rows(PLAN_TEXT)[number]) == is_proposed_in_file(adr), (
        number
    )


def test_plano_nao_cita_pr_como_aberto_ou_em_revisao() -> None:
    assert re.findall(r"PR #\d+,? ainda aberto|\*Em revisão \(PR #\d+\)", PLAN_TEXT) == []


def test_secao_de_fatias_tem_estado_datado() -> None:
    section = PLAN_TEXT.split('<a id="s3-9"></a>')[1].split('<a id="s3-10"></a>')[0]
    assert re.search(r"^\*\*Estado em \d{4}-\d{2}-\d{2}:\*\*", section, re.MULTILINE)


def test_historico_depois_de_antes_nao_conta_como_proposto() -> None:
    adr = "- **Estado:** aceito no merge do PR #1 (2026-01-01); antes: proposto\n- **Plano:** x\n"
    assert not is_proposed_in_file(adr)


def test_status_sem_linha_de_estado_vale_a_secao() -> None:
    adr = "# ADR\n\n## Status\n\nProposto, à espera.\n\n## Contexto\n\naceito\n"
    assert is_proposed_in_file(adr)
