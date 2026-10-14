"""Diretório de saídas no filesystem (docs/GoblinShiftPlan.md, 3.6 e 1.7).

Regras de nome: NFC, extensão preservada, até 180 bytes por componente, colisão comparada por
`casefold()` do caminho completo depois do NFC e resolvida com sufixo pelo índice original da
entrada, nomes reservados na raiz (`manifest.json`, `report.json` e, passado pelo executor, o
`<recipe_id>.zip`), nunca sobrescrever. Tetos: quantidade de arquivos e bytes somados entre a raiz
e todos os grupos.

Grupos (ADR 019): um nível de subpasta, só com os nomes de `OutputGroup`, criado na primeira
saída do grupo. O caminho mais longo possível (`thumbnails/` + 180 bytes) fica abaixo dos 240
bytes de 3.6, que é também o teto de `artifact.file_name` no banco.
"""

import contextlib
import io
import itertools
import stat
import unicodedata
from collections.abc import Buffer, Iterator, Sequence
from pathlib import Path

from goblinshift.processing.names import has_unsafe_char
from goblinshift.processing.recipe import (
    FailureCode,
    OutputFile,
    OutputGroup,
    OutputRole,
    RecipeFailure,
)

MIB = 1024 * 1024
MAX_COMPONENT_BYTES = 180
MAX_PATH_BYTES = 240
RESERVED_NAMES = ("manifest.json", "report.json")
DEFAULT_MAX_FILES = 220
DEFAULT_MAX_BYTES = 480 * MIB
_SEPARATORS = ("/", "\\")
_TRAVERSAL = (".", "..")


class InvalidOutputNameError(ValueError):
    """A receita pediu um nome que não pode ser componente de caminho (erro de programação)."""


class OutputGroupError(Exception):
    """A subpasta do grupo já existe e não é um diretório real (symlink ou arquivo)."""


class FileSystemOutputDir:
    """Cria saídas num diretório existente e controlado pelo executor."""

    def __init__(
        self,
        root: Path,
        *,
        reserved_names: Sequence[str] = RESERVED_NAMES,
        max_files: int = DEFAULT_MAX_FILES,
        max_bytes: int = DEFAULT_MAX_BYTES,
    ) -> None:
        self._root = root
        self._max_files = max_files
        self._budget = _ByteBudget(max_bytes)
        self._taken = {name.casefold() for name in reserved_names}
        self._ready_groups: set[OutputGroup] = set()
        self._created: list[tuple[str, OutputRole]] = []

    @property
    def created(self) -> tuple[tuple[str, OutputRole], ...]:
        return tuple(self._created)

    def create(
        self,
        logical_name: str,
        role: OutputRole,
        position: int,
        group: OutputGroup | None = None,
    ) -> OutputFile:
        if len(self._created) == self._max_files:
            raise RecipeFailure(
                FailureCode.OUTPUT_LIMIT_EXCEEDED,
                f"a execução passaria de {self._max_files} arquivos de saída",
            )
        component = _validated_nfc(logical_name)
        prefix = "" if group is None else f"{self._ensure_group(group)}/"
        path = self._claim_unique(prefix, component, position)
        stream = _MeteredWriter(self._root / path, self._budget)
        self._created.append((path, role))
        return OutputFile(path, stream)

    def _ensure_group(self, group: OutputGroup) -> str:
        if group not in self._ready_groups:
            directory = self._root / group.value
            # O que já existir com o nome do grupo é decidido pelo lstat, que não segue symlink:
            # a saída nunca é escrita fora de out/.
            with contextlib.suppress(FileExistsError):
                directory.mkdir()
            if not stat.S_ISDIR(directory.lstat().st_mode):
                raise OutputGroupError(f"{group.value} em out/ não é um diretório real")
            self._ready_groups.add(group)
        return group.value

    def _claim_unique(self, prefix: str, name: str, position: int) -> str:
        stem, extension = _split_extension(name)
        candidates = (
            prefix + _fit(stem, suffix, extension) for suffix in _collision_suffixes(position)
        )
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
    if has_unsafe_char(normalized):
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
