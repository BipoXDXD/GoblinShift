"""Relatório do SLI e do orçamento de erro na janela deslizante (3.7)."""

import pytest

from openobserve.catalog import slo_named
from openobserve.report import SliReport

ACCEPTANCE = slo_named("aceitacao")  # 99,5%: orçamento de 1 em 200


def test_sli_e_orcamento_restante_calculados_a_mao() -> None:
    # 1000 elegíveis, 997 bons: SLI 99,7%; orçamento de 5 ruins, 3 gastos, sobram 40%.
    report = SliReport(ACCEPTANCE, eligible=1000, good=997)

    assert report.sli == pytest.approx(0.997)
    assert report.budget_remaining == pytest.approx(0.4)
    assert report.exhausted is False


def test_orcamento_exatamente_gasto_esta_esgotado() -> None:
    report = SliReport(ACCEPTANCE, eligible=1000, good=995)

    assert report.budget_remaining == pytest.approx(0.0)
    assert report.exhausted is True


def test_orcamento_estourado_fica_negativo() -> None:
    report = SliReport(ACCEPTANCE, eligible=200, good=197)

    assert report.budget_remaining == pytest.approx(-2.0)
    assert report.exhausted is True


def test_sem_eventos_nao_ha_sli_nem_orcamento_gasto() -> None:
    report = SliReport(ACCEPTANCE, eligible=0, good=0)

    assert report.sli is None
    assert report.budget_remaining == 1.0
    assert report.exhausted is False


def test_linha_do_relatorio_mostra_meta_sli_e_orcamento() -> None:
    line = SliReport(ACCEPTANCE, eligible=1000, good=997).line()

    assert line == "aceitacao: 997/1000 bons, SLI 99.700% (meta 99.500%), orçamento restante 40.0%"


def test_linha_sem_eventos() -> None:
    line = SliReport(ACCEPTANCE, eligible=0, good=0).line()

    assert line == "aceitacao: sem eventos elegíveis na janela (meta 99.500%)"
