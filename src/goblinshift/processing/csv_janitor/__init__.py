"""CSV Janitor: limpa tabelas sem alterar seu significado por adivinhação.

Biblioteca pura, só com a biblioteca padrão (docs/GoblinShiftPlan.md, 1.3 e fatia 2 em 3.9).
"""

from goblinshift.processing.csv_janitor.janitor import clean_table
from goblinshift.processing.csv_janitor.limits import Limits
from goblinshift.processing.csv_janitor.params import (
    CaseMode,
    CaseRule,
    Delimiter,
    Filter,
    FilterOp,
    FormulaGuard,
    InvalidParametersError,
    Params,
    params_from_mapping,
)
from goblinshift.processing.csv_janitor.results import (
    Counts,
    RejectionCode,
    TableCleaned,
    TableRejected,
    TableWarning,
    WarningCode,
)

__all__ = [
    "CaseMode",
    "CaseRule",
    "Counts",
    "Delimiter",
    "Filter",
    "FilterOp",
    "FormulaGuard",
    "InvalidParametersError",
    "Limits",
    "Params",
    "RejectionCode",
    "TableCleaned",
    "TableRejected",
    "TableWarning",
    "WarningCode",
    "clean_table",
    "params_from_mapping",
]
