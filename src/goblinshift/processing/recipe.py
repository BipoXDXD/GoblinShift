"""Contrato entre o executor e as receitas (docs/GoblinShiftPlan.md, 3.2).

A classe da falha sai do tipo: `ItemRejected` é rejeição de um arquivo no relatório,
`RecipeFailure` é falha determinística da execução e qualquer outra exceção é infraestrutura.

Diferenças em relação ao esboço de 3.2, todas aditivas:
- `OutputDir.create` recebe a posição da entrada, porque 3.6 resolve colisões de nome "com
  sufixo pelo índice original", e devolve o nome final junto do arquivo, porque o `ItemOk`
  precisa citar o nome que de fato foi criado.
- `ItemOk` carrega avisos (códigos de `ItemWarningCode`) e contagens da própria entrada: o
  relatório informa contagens e avisos por arquivo (1.1, 1.3), e `Report.warnings` fica para o
  que vale para a execução inteira.

Fatia 5 (etapa 0, também aditiva):
- `details`, no item e no relatório, registra fatos que não são aviso (dimensões, formato, perfil
  de cor convertido, tipo do Detox) e por isso não mudam o estado para `succeeded_with_warnings`.
  Vão só para o `report.json`; o manifesto não os traz.
- `ItemDuplicate` é o terceiro desfecho de um item (Downloads Detox, 1.4). Como tipo próprio, e
  não um campo opcional do `ItemOk`, a "duplicata com saídas" não tem como ser representada.
- `OutputDir.create` aceita um `OutputGroup`: a subpasta de `out/` e do ZIP (1.2 e 1.4), de uma
  lista fechada que a coleta do worker também impõe (ADR 019).
"""

import re
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


class ItemWarningCode(StrEnum):
    """Códigos de aviso das receitas: lista fechada, como a das rejeições.

    O código vai para o banco e para a API (detalhe da Run); o `detail`, que pode citar valores do
    arquivo, fica só no `report.json`. Receita que avisa algo novo acrescenta o código aqui.
    """

    CSV_ALL_RECORDS_FILTERED = "CSV_ALL_RECORDS_FILTERED"
    CSV_FORMULA_CELLS = "CSV_FORMULA_CELLS"
    CSV_FORMULA_HEADER = "CSV_FORMULA_HEADER"


@dataclass(frozen=True)
class ReportWarning:
    code: str  # de `ItemWarningCode`
    detail: str

    def __post_init__(self) -> None:
        if self.code not in ItemWarningCode:
            raise ValueError(f"{self.code!r} não é código de aviso do catálogo")


type DetailValue = str | int | bool


class OutputGroup(StrEnum):
    """Subpastas permitidas em `out/` e no ZIP (1.2 e 1.4).

    Lista fechada: a coleta do worker só aceita estas.
    """

    IMAGES = "images"
    THUMBNAILS = "thumbnails"
    TABLES = "tables"
    TEXT = "text"
    ARCHIVES = "archives"
    # Derivados para pré-visualização (imagem reduzida, amostra de tabela): publicados com o papel
    # `preview`, fora do manifesto e do ZIP.
    PREVIEWS = "previews"


@dataclass(frozen=True)
class Preview:
    """Arquivo de pré-visualização em `previews/` e a saída de conteúdo de que ele deriva."""

    path: str
    of: str

    def __post_init__(self) -> None:
        if not self.path.startswith(f"{OutputGroup.PREVIEWS.value}/"):
            raise ValueError(f"preview fora de {OutputGroup.PREVIEWS.value}/: {self.path!r}")


# Contagens por item vão para o banco e para a API: nome de contador em snake_case, nunca dado do
# arquivo (como um nome de coluna), e no máximo 16 por item.
ITEM_COUNT_NAME = re.compile(r"[a-z][a-z0-9_]{0,39}")
MAX_ITEM_COUNTS = 16


@dataclass(frozen=True)
class ItemOk:
    position: int
    # Caminhos finais relativos a out/, como `OutputDir.create` devolveu: "a.csv" ou
    # "images/foto.jpg". A ordem aqui é a ordem no ZIP e no manifesto.
    outputs: Sequence[str]
    warnings: Sequence[ReportWarning] = ()
    # Contagens próprias da receita (no CSV Janitor, registros lidos, filtrados etc.).
    counts: Mapping[str, int] = field(default_factory=dict)
    # Fatos registrados que não são aviso (não mudam o estado para succeeded_with_warnings):
    # dimensões, formato, perfil assumido ou convertido, quadros MPO descartados, tipo do Detox.
    details: Mapping[str, DetailValue] = field(default_factory=dict)
    # Opcionais: a receita grava os que cabem nos tetos depois de todo o conteúdo.
    previews: Sequence[Preview] = ()

    def __post_init__(self) -> None:
        for preview in self.previews:
            if preview.of not in self.outputs:
                raise ValueError(f"preview de {preview.of!r}, que não é saída do item")


class ItemRejectionCode(StrEnum):
    """Códigos de rejeição por item do catálogo de 1.6: lista fechada.

    Cada receita usa um subconjunto. A coleta do worker recusa no `report.json` qualquer outro
    código, porque o relatório vem do sandbox e o código vai para o banco e para a API.
    """

    UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"
    UNSUPPORTED_BIT_DEPTH = "UNSUPPORTED_BIT_DEPTH"
    UNSUPPORTED_COLOR_PROFILE = "UNSUPPORTED_COLOR_PROFILE"
    IMAGE_TOO_LARGE = "IMAGE_TOO_LARGE"
    CSV_STRUCTURE_INVALID = "CSV_STRUCTURE_INVALID"
    FIELD_LIMIT_EXCEEDED = "FIELD_LIMIT_EXCEEDED"


@dataclass(frozen=True)
class ItemRejected:
    position: int
    code: str  # de `ItemRejectionCode`
    reason: str

    def __post_init__(self) -> None:
        if self.code not in ItemRejectionCode:
            raise ValueError(f"{self.code!r} não é código de rejeição do catálogo")


@dataclass(frozen=True)
class ItemDuplicate:
    """Detox: mesmo SHA-256 e tamanho de uma entrada anterior do lote (1.4); não gera saída."""

    position: int
    duplicate_of: int  # posição da primeira ocorrência; sempre < position

    def __post_init__(self) -> None:
        if not 0 <= self.duplicate_of < self.position:
            raise ValueError(f"duplicate_of {self.duplicate_of} inválido para {self.position}")


type ReportItem = ItemOk | ItemRejected | ItemDuplicate


@dataclass(frozen=True)
class Report:
    items: Sequence[ReportItem]  # um por InputFile, na ordem
    warnings: Sequence[ReportWarning] = ()
    # Fatos da execução inteira, só para o report.json (ex.: {"resampling": "lanczos"}).
    details: Mapping[str, DetailValue] = field(default_factory=dict)

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


def require_max_inputs(inputs: Sequence[InputFile], max_inputs: int, noun: str) -> None:
    """Recusa o lote acima do `max_inputs` da versão; `noun` nomeia as entradas na mensagem."""
    if len(inputs) > max_inputs:
        raise InputContractError(f"{len(inputs)} {noun}; o limite por execução é {max_inputs}")


class OutputRole(StrEnum):
    CONTENT = "content"
    PREVIEW = "preview"


@dataclass(frozen=True)
class OutputFile:
    # Caminho final relativo a out/, já com as regras de 3.6: "a.csv" ou "images/foto.jpg".
    name: str
    stream: BinaryIO


class OutputDir(Protocol):
    def create(
        self,
        logical_name: str,
        role: OutputRole,
        position: int,
        group: OutputGroup | None = None,
    ) -> OutputFile:
        """Cria a saída na raiz de out/ ou, com `group`, na subpasta do grupo.

        `logical_name` é um componente só (sem separador). `OutputFile.name` é o caminho final
        relativo a out/ ("images/foto.jpg"). Aplica as regras de nome de 3.6 dentro do grupo
        (colisão por `casefold()` do caminho completo; nomes reservados só na raiz) e os tetos de
        bytes e quantidade de 1.7, somados entre todos os grupos. Exceder um teto levanta
        `RecipeFailure(OUTPUT_LIMIT_EXCEEDED)`.
        """
        ...

    def room_for(self, size_bytes: int) -> bool:
        """Se mais um arquivo de `size_bytes` cabe nos tetos; para saídas opcionais (previews)."""
        ...


class Progress(Protocol):
    def item_done(self, position: int) -> None: ...


class Recipe[P](Protocol):
    """P é o modelo de parâmetros validados da versão da receita."""

    def run(
        self, params: P, inputs: Sequence[InputFile], out: OutputDir, progress: Progress
    ) -> Report: ...
