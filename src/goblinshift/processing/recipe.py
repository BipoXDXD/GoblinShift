"""Contrato entre o executor e as receitas (docs/GoblinShiftPlan.md, 3.2).

A classe da falha sai do tipo: `ItemRejected` é rejeição de um arquivo no relatório,
`RecipeFailure` é falha determinística da execução e qualquer outra exceção é infraestrutura.

Diferenças em relação ao esboço de 3.2, todas aditivas:
- `OutputDir.create` recebe a posição da entrada, porque 3.6 resolve colisões de nome "com
  sufixo pelo índice original", e devolve o nome final junto do arquivo, porque o `ItemOk`
  precisa citar o nome que de fato foi criado.
- `ItemOk` carrega avisos e contagens da própria entrada: o relatório informa contagens e avisos
  por arquivo (1.1, 1.3), e `Report.warnings` fica para o que vale para a execução inteira.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import BinaryIO, Protocol


@dataclass(frozen=True)
class InputFile:
    position: int  # ordem da entrada, a partir de 0
    path: Path  # somente leitura, dentro do sandbox
    original_name: str  # dado, nunca caminho
    size: int
    sha256: str


@dataclass(frozen=True)
class ReportWarning:
    code: str
    detail: str


@dataclass(frozen=True)
class ItemOk:
    position: int
    outputs: Sequence[str]  # nomes finais criados em OutputDir
    warnings: Sequence[ReportWarning] = ()
    # Contagens próprias da receita (no CSV Janitor, registros lidos, filtrados etc.).
    counts: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class ItemRejected:
    position: int
    code: str  # código do catálogo de 1.6
    reason: str


@dataclass(frozen=True)
class Report:
    items: Sequence[ItemOk | ItemRejected]  # um por InputFile, na ordem
    warnings: Sequence[ReportWarning] = ()

    @property
    def has_outputs(self) -> bool:
        """Sem nenhum item aceito, a publicação termina `failed` com `NO_VALID_OUTPUTS` (3.5)."""
        return any(isinstance(item, ItemOk) for item in self.items)


class FailureCode(StrEnum):
    """Falhas determinísticas do catálogo de 1.6 que nascem no executor."""

    # Decidido pelo worker na publicação, a partir do relatório (`Report.has_outputs`).
    NO_VALID_OUTPUTS = "NO_VALID_OUTPUTS"
    OUTPUT_LIMIT_EXCEEDED = "OUTPUT_LIMIT_EXCEEDED"
    REPORT_LIMIT_EXCEEDED = "REPORT_LIMIT_EXCEEDED"


class RecipeFailure(Exception):  # noqa: N818 - nome fixado pelo plano (1.6 e 3.2)
    """Falha determinística: repetir a tentativa daria o mesmo resultado."""

    def __init__(self, code: FailureCode, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


class InputContractError(ValueError):
    """Entradas fora do que a versão da receita aceita (por exemplo, acima de `max_inputs`).

    A aceitação já recusa esse comando com 422; chegar à receita é job fora do contrato, um
    defeito de infraestrutura, e não falha determinística do conteúdo.
    """


class OutputRole(StrEnum):
    CONTENT = "content"
    PREVIEW = "preview"


@dataclass(frozen=True)
class OutputFile:
    name: str  # nome final, já com as regras de 3.6 aplicadas
    stream: BinaryIO


class OutputDir(Protocol):
    def create(self, logical_name: str, role: OutputRole, position: int) -> OutputFile:
        """Aplica as regras de nome de 3.6 e os tetos de bytes e quantidade de 1.7.

        Exceder um teto levanta `RecipeFailure(OUTPUT_LIMIT_EXCEEDED)`.
        """
        ...


class Progress(Protocol):
    def item_done(self, position: int) -> None: ...


class Recipe[P](Protocol):
    """P é o modelo de parâmetros validados da versão da receita."""

    def run(
        self, params: P, inputs: Sequence[InputFile], out: OutputDir, progress: Progress
    ) -> Report: ...
