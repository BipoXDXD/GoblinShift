"""Coleta do que o executor deixou em `out/` (docs/GoblinShiftPlan.md, 3.6 e 1.7).

O executor é código que processa conteúdo do usuário: o que ele escreve é dado não confiável.
A coleta só aceita arquivos regulares diretamente em `out/`, sem symlinks nem hardlinks, conta
quantidade e bytes enquanto lê, e confere o `report.json` contra um schema próprio. Cada arquivo
de conteúdo precisa estar citado no relatório, e cada citação precisa existir.

Duas classes de recusa:
- `OutputLimitError`: teto de 1.7 estourado, falha determinística (repetir daria o mesmo);
- `CollectionError`: executor fora do contrato (link, diretório, relatório inválido), tratado
  como infraestrutura, com retentativa.
"""

import json
import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from goblinshift.processing.executor.report_file import REPORT_MAX_BYTES, REPORT_SCHEMA_VERSION
from goblinshift.processing.output_dir import DEFAULT_MAX_BYTES, DEFAULT_MAX_FILES
from goblinshift.runs.lifecycle import ErrorCode, PublishOutcome

REPORT_NAME = "report.json"
_ITEM_OK = "ok"
_ITEM_REJECTED = "rejected"


class CollectionError(Exception):
    """`out/` fora do contrato do executor."""


class OutputLimitError(Exception):
    def __init__(self, code: ErrorCode, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code


@dataclass(frozen=True)
class OutputLimits:
    """Tetos de 1.7: 220 arquivos de conteúdo, 480 MiB somados e 8 MiB por relatório."""

    max_files: int = DEFAULT_MAX_FILES
    max_bytes: int = DEFAULT_MAX_BYTES
    report_max_bytes: int = REPORT_MAX_BYTES


@dataclass(frozen=True)
class OutputFile:
    name: str
    path: Path
    size: int


@dataclass(frozen=True)
class ReportItem:
    position: int
    accepted: bool
    outputs: tuple[str, ...]
    # Código da rejeição (catálogo de 1.6); `None` para item aceito.
    code: str | None
    has_warnings: bool


@dataclass(frozen=True)
class ParsedReport:
    items: tuple[ReportItem, ...]
    has_global_warnings: bool
    summary: Mapping[str, int]

    @property
    def outcome(self) -> PublishOutcome:
        if not any(item.accepted for item in self.items):
            return PublishOutcome.NO_VALID_OUTPUTS
        if self.has_global_warnings or any(
            not item.accepted or item.has_warnings for item in self.items
        ):
            return PublishOutcome.SUCCEEDED_WITH_WARNINGS
        return PublishOutcome.SUCCEEDED


@dataclass(frozen=True)
class Collected:
    # Conteúdo na ordem do relatório (entrada, depois saída dentro da entrada).
    outputs: tuple[OutputFile, ...]
    report_file: OutputFile
    report: ParsedReport


DEFAULT_LIMITS = OutputLimits()


def collect_outputs(
    out_dir: Path, input_count: int, limits: OutputLimits = DEFAULT_LIMITS
) -> Collected:
    files = _scan(out_dir, limits)
    report_file = files.pop(REPORT_NAME, None)
    if report_file is None:
        raise CollectionError(f"{REPORT_NAME} ausente em out/")
    if report_file.size > limits.report_max_bytes:
        raise OutputLimitError(
            ErrorCode.REPORT_LIMIT_EXCEEDED,
            f"relatório com {report_file.size} bytes; o teto é {limits.report_max_bytes}",
        )
    report = parse_report(_read_no_follow(report_file.path, limits.report_max_bytes), input_count)
    cited = [name for item in report.items for name in item.outputs]
    if len(set(cited)) != len(cited) or set(cited) != set(files):
        raise CollectionError("os arquivos de out/ não batem com as saídas citadas no relatório")
    return Collected(tuple(files[name] for name in cited), report_file, report)


def parse_report(data: bytes, input_count: int) -> ParsedReport:
    """Confere o `report.json` contra o schema 1 documentado no pacote do executor."""
    try:
        raw = json.loads(data, object_pairs_hook=_reject_duplicates, parse_constant=_reject)
    except ValueError as error:
        raise CollectionError(f"relatório não é JSON aceito: {error}") from None
    document = _mapping(raw, "relatório")
    if document.get("schema_version") != REPORT_SCHEMA_VERSION:
        raise CollectionError("relatório com schema_version desconhecida")
    items = tuple(_item(raw_item) for raw_item in _list(document.get("items"), "items"))
    if [item.position for item in items] != list(range(input_count)):
        raise CollectionError(f"relatório deve ter as posições 0..{input_count - 1}, na ordem")
    summary = _mapping(document.get("summary"), "summary")
    counts = {key: _int(value, f"summary.{key}") for key, value in summary.items()}
    warnings = _list(document.get("warnings"), "warnings")
    return ParsedReport(items, bool(warnings), counts)


def _scan(out_dir: Path, limits: OutputLimits) -> dict[str, OutputFile]:
    try:
        info = out_dir.lstat()
    except FileNotFoundError:
        raise CollectionError("out/ ausente") from None
    if not stat.S_ISDIR(info.st_mode):
        raise CollectionError("out/ não é diretório regular")
    files: dict[str, OutputFile] = {}
    content_bytes = 0
    with os.scandir(out_dir) as entries:
        for entry in entries:
            output = _regular_file(entry)
            files[output.name] = output
            if output.name == REPORT_NAME:
                continue
            content_bytes += output.size
            # Conferido a cada arquivo: um diretório com milhões de entradas não é lido inteiro.
            if len(files) - (REPORT_NAME in files) > limits.max_files:
                raise OutputLimitError(
                    ErrorCode.OUTPUT_LIMIT_EXCEEDED, f"mais de {limits.max_files} arquivos"
                )
            if content_bytes > limits.max_bytes:
                raise OutputLimitError(
                    ErrorCode.OUTPUT_LIMIT_EXCEEDED, f"saídas acima de {limits.max_bytes} bytes"
                )
    return files


def _regular_file(entry: os.DirEntry[str]) -> OutputFile:
    info = entry.stat(follow_symlinks=False)
    if not stat.S_ISREG(info.st_mode):
        raise CollectionError(f"out/ contém algo que não é arquivo regular: {entry.name!r}")
    if info.st_nlink != 1:
        raise CollectionError(f"out/ contém hardlink: {entry.name!r}")
    return OutputFile(entry.name, Path(entry.path), info.st_size)


def _read_no_follow(path: Path, max_bytes: int) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as source:
        data = source.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise OutputLimitError(ErrorCode.REPORT_LIMIT_EXCEEDED, "relatório cresceu além do teto")
    return data


def _item(raw: object) -> ReportItem:
    item = _mapping(raw, "item")
    position = _int(item.get("position"), "item.position")
    status = item.get("status")
    if status == _ITEM_OK:
        outputs = tuple(_text(name, "item.outputs[]") for name in _list(item.get("outputs"), "o"))
        warnings = _list(item.get("warnings"), "item.warnings")
        return ReportItem(position, True, outputs, None, bool(warnings))
    if status == _ITEM_REJECTED:
        code = _text(item.get("code"), "item.code")
        return ReportItem(position, False, (), code, False)
    raise CollectionError(f"item com status desconhecido: {status!r}")


def _mapping(raw: object, where: str) -> dict[str, object]:
    if not isinstance(raw, dict):
        raise CollectionError(f"{where} deve ser objeto")
    return raw


def _list(raw: object, where: str) -> list[object]:
    if not isinstance(raw, list):
        raise CollectionError(f"{where} deve ser lista")
    return raw


def _int(raw: object, where: str) -> int:
    if not isinstance(raw, int) or isinstance(raw, bool):
        raise CollectionError(f"{where} deve ser inteiro")
    return raw


def _text(raw: object, where: str) -> str:
    if not isinstance(raw, str) or not raw:
        raise CollectionError(f"{where} deve ser texto não vazio")
    return raw


def _reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("chaves repetidas")
    return result


def _reject(name: str) -> object:
    raise ValueError(f"{name} não é um valor JSON aceito")
