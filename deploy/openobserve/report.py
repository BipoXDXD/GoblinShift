"""SLI e orçamento de erro na janela deslizante, para o relatório da linha de comando (3.7).

A política de orçamento de 3.7 diz que, com o orçamento de qualquer SLO esgotado, a próxima
fatia não começa: o relatório é a forma de conferir isso antes de começar.
"""

from dataclasses import dataclass

from openobserve.catalog import Slo


@dataclass(frozen=True)
class SliReport:
    slo: Slo
    eligible: int
    good: int

    @property
    def sli(self) -> float | None:
        return self.good / self.eligible if self.eligible else None

    @property
    def budget_remaining(self) -> float:
        """Fração do orçamento que sobra: 1 sem falhas, 0 esgotado, negativa se estourou."""
        if not self.eligible:
            return 1.0
        allowed_bad = self.eligible / self.slo.budget_denominator
        return 1 - (self.eligible - self.good) / allowed_bad

    @property
    def exhausted(self) -> bool:
        return self.budget_remaining <= 0

    def line(self) -> str:
        target = f"meta {self.slo.target:.3%}"
        if self.sli is None:
            return f"{self.slo.name}: sem eventos elegíveis na janela ({target})"
        return (
            f"{self.slo.name}: {self.good}/{self.eligible} bons, SLI {self.sli:.3%} ({target}), "
            f"orçamento restante {self.budget_remaining:.1%}"
        )
