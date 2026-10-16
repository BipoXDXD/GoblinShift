"""Limites do PDF Press (docs/GoblinShiftPlan.md, 1.5 e 1.7; ADR 035)."""

from dataclasses import dataclass

MIB = 1024 * 1024
RECIPE_ID = "pdf-press"
VERSION = 1
MAX_INPUTS = 20
# Somadas entre todas as entradas, contadas depois de abrir cada documento.
MAX_INPUT_PAGES = 200
MAX_OUTPUT_PAGES = 400
MAX_OUTPUT_DOCUMENTS = 200
# Teto da soma das entradas, imposto na aceitação (ADR 035): o `merge` copia quase todos os bytes
# de cada entrada para a saída, e metade dos 480 MiB de conteúdo de 1.7 deixa folga para o
# `split`, que repete recursos compartilhados em cada parte.
MAX_TOTAL_INPUT_BYTES = 256 * MIB
# Objetos por documento, pelo /Size do trailer e pela contagem real: um PDF de 200 páginas comum
# fica em poucos milhares; acima disto é tratado como bomba de objetos.
MAX_OBJECTS_PER_DOCUMENT = 100_000


@dataclass(frozen=True)
class Limits:
    """Tetos de uma execução; os testes passam valores menores para chegar à fronteira."""

    max_input_pages: int = MAX_INPUT_PAGES
    max_output_pages: int = MAX_OUTPUT_PAGES
    max_output_documents: int = MAX_OUTPUT_DOCUMENTS
    max_objects_per_document: int = MAX_OBJECTS_PER_DOCUMENT
