"""Diretório de saídas no filesystem (docs/GoblinShiftPlan.md, 3.6 e 1.7).

Regras de nome: NFC, extensão preservada, até 180 bytes por componente, colisão comparada por
`casefold()` depois do NFC e resolvida com sufixo pelo índice original da entrada, `manifest.json`
e `report.json` reservados, nunca sobrescrever. Tetos: quantidade de arquivos e bytes somados.
"""

import io
import itertools
import unicodedata
from collections.abc import Buffer, Iterator
from pathlib import Path

from goblinshift.processing.recipe import FailureCode, OutputFile, OutputRole, RecipeFailure

MIB = 1024 * 1024
MAX_COMPONENT_BYTES = 180
RESERVED_NAMES = ("manifest.json", "report.json")
DEFAULT_MAX_FILES = 220
DEFAULT_MAX_BYTES = 480 * MIB
_SEPARATORS = ("/", "\\")
_TRAVERSAL = (".", "..")


class InvalidOutputNameError(ValueError):
    """A receita pediu um nome que não pode ser componente de caminho (erro de programação)."""


class FileSystemOutputDir:
    """Cria saídas num diretório existente e controlado pelo executor."""

    def __init__(
        self,
        root: Path,
        *,
        max_files: int = DEFAULT_MAX_FILES,
        max_bytes: int = DEFAULT_MAX_BYTES,
    ) -> None:
        self._root = root
        self._max_files = max_files
        self._budget = _ByteBudget(max_bytes)
        self._taken = {name.casefold() for name in RESERVED_NAMES}
        self._created: list[tuple[str, OutputRole]] = []

    @property
    def created(self) -> tuple[tuple[str, OutputRole], ...]:
        return tuple(self._created)

    def create(self, logical_name: str, role: OutputRole, position: int) -> OutputFile:
        if len(self._created) == self._max_files:
            raise RecipeFailure(
                FailureCode.OUTPUT_LIMIT_EXCEEDED,
                f"a execução passaria de {self._max_files} arquivos de saída",
            )
        name = self._claim_unique(_validated_nfc(logical_name), position)
        stream = _MeteredWriter(self._root / name, self._budget)
        self._created.append((name, role))
        return OutputFile(name, stream)

    def _claim_unique(self, name: str, position: int) -> str:
        stem, extension = _split_extension(name)
        candidates = (_fit(stem, suffix, extension) for suffix in _collision_suffixes(position))
        chosen = next(c for c in candidates if c.casefold() not in self._taken)
        self._taken.add(chosen.casefold())
        return chosen


def _collision_suffixes(position: int) -> Iterator[str]:
    """Sem sufixo, depois o índice original da entrada e, se ainda colidir, um contador."""
    yield ""
    yield f"_{position}"
    for counter in itertools.count(2):
        yield f"_{position}_{counter}"


def _validated_nfc(name: str) -> str:
    normalized = unicodedata.normalize("NFC", name)
    if normalized in ("", *_TRAVERSAL):
        raise InvalidOutputNameError(f"nome de saída inválido: {name!r}")
    if any(separator in normalized for separator in _SEPARATORS):
        raise InvalidOutputNameError(f"nome de saída com separador: {name!r}")
    if any(unicodedata.category(char) == "Cc" for char in normalized):
        raise InvalidOutputNameError(f"nome de saída com caractere de controle: {name!r}")
    return normalized


def _split_extension(name: str) -> tuple[str, str]:
    """Separa a extensão no último ponto; um ponto inicial (`.config`) não é extensão."""
    stem, dot, extension = name.rpartition(".")
    if not dot or not stem:
        return name, ""
    return stem, f".{extension}"


def _fit(stem: str, suffix: str, extension: str) -> str:
    """Encurta o radical, caractere a caractere, até o nome caber em 180 bytes UTF-8."""
    tail = suffix + extension
    room = MAX_COMPONENT_BYTES - len(tail.encode())
    if room < 0:
        raise InvalidOutputNameError(f"extensão longa demais: {extension!r}")
    while len(stem.encode()) > room:
        stem = stem[:-1]
    return stem + tail


class _ByteBudget:
    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._used = 0

    def spend(self, size: int) -> None:
        if self._used + size > self._limit:
            raise RecipeFailure(
                FailureCode.OUTPUT_LIMIT_EXCEEDED,
                f"as saídas passariam de {self._limit} bytes",
            )
        self._used += size


class _MeteredWriter(io.BufferedWriter):
    """Arquivo novo e exclusivo que só aceita bytes dentro do orçamento da execução."""

    def __init__(self, path: Path, budget: _ByteBudget) -> None:
        # "xb" abre com O_CREAT | O_EXCL: um arquivo, ou symlink, já existente nunca é reescrito.
        super().__init__(io.FileIO(path, "xb"))
        self._budget = budget

    def write(self, data: Buffer, /) -> int:
        # Conferido antes de bufferizar, para o excesso nunca chegar ao disco.
        self._budget.spend(memoryview(data).nbytes)
        return super().write(data)
