"""Coleta do que o executor deixou em `out/` (docs/GoblinShiftPlan.md, 3.6 e 1.7).

O executor é código que processa conteúdo do usuário: o que ele escreve é dado não confiável.
A coleta só aceita arquivos regulares em `out/` ou num único nível de subpasta com nome de
`OutputGroup` (lista fechada, ADR 019), sem symlinks nem hardlinks, conta quantidade e bytes
enquanto lê, e confere o `report.json` contra um schema próprio. Na raiz, `manifest.json` e
`<recipe_id>.zip` (fatia 5) ficam fora do orçamento de conteúdo, cada um com o seu teto de 1.7, e
vêm juntos ou não vêm: o executor anterior à fatia 5, fixado em Runs aceitas antes do deploy, não
empacota. Cada arquivo de conteúdo precisa estar citado no relatório pelo caminho relativo a
`out/` ("images/a.jpg"), e cada citação precisa existir. A coleta roda depois que o executor
terminou: ninguém mais escreve em `out/`.

Cada arquivo aceito sai com o SHA-256 que o worker calculou; manifesto e ZIP precisam bater com
eles (`package_check`). Ler tudo isso leva tempo (o ZIP chega a 512 MiB): `heartbeat` é chamado a
cada bloco lido, para o coordenador renovar o lease.

O que do relatório vai para o banco e para a API sai em tipo próprio (parse, don't validate): o
`code` de cada rejeição, só do catálogo de 1.6 (`ItemRejectionCode`), e o `summary`, só com as
chaves conhecidas e com os valores que os próprios itens dão. Sem essa conferência, um executor
comprometido gravaria texto arbitrário em `run_input.error_code` e em `run.summary`.

Duas classes de recusa:
- `OutputLimitError`: teto de 1.7 estourado, falha determinística (repetir daria o mesmo);
- `CollectionError`: executor fora do contrato (link, diretório, relatório inválido), tratado
  como infraestrutura, com retentativa.
"""

import hashlib
import os
import stat
import unicodedata
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from goblinshift.processing.executor.report_file import REPORT_MAX_BYTES, REPORT_SCHEMA_VERSION
from goblinshift.processing.names import has_unsafe_char
from goblinshift.processing.output_dir import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_FILES,
    MAX_COMPONENT_BYTES,
    MAX_PATH_BYTES,
)
from goblinshift.processing.packaging import (
    MANIFEST_NAME,
    REPORT_NAME,
    PackageLimits,
    package_name_for,
)
from goblinshift.processing.recipe import ItemRejectionCode, OutputGroup
from goblinshift.runs.lifecycle import ErrorCode, PublishOutcome
from goblinshift.runs.worker.package_check import (
    ManifestInput,
    PackageError,
    verify_manifest,
    verify_zip,
)
from goblinshift.runs.worker.strict_json import parse_strict_json

_PACKAGE_LIMITS = PackageLimits()
READ_CHUNK_BYTES = 1024 * 1024
_ITEM_OK = "ok"
_ITEM_REJECTED = "rejected"
_ITEM_DUPLICATE = "duplicate"
_GROUPS = frozenset(group.value for group in OutputGroup)
_SUMMARY_REQUIRED_KEYS = frozenset({"inputs", "ok", "rejected", "outputs"})
# `duplicates` entrou na fatia 5; o executor anterior, fixado em Runs antigas, não o escreve.
_SUMMARY_KEYS = _SUMMARY_REQUIRED_KEYS | {"duplicates"}


class CollectionError(Exception):
    """`out/` fora do contrato do executor."""


class OutputLimitError(Exception):
    def __init__(self, code: ErrorCode, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code


@dataclass(frozen=True)
class OutputLimits:
    """Tetos de 1.7: 220 arquivos de conteúdo, 480 MiB somados, 8 MiB por relatório ou manifesto
    e 512 MiB para o ZIP."""

    max_files: int = DEFAULT_MAX_FILES
    max_bytes: int = DEFAULT_MAX_BYTES
    report_max_bytes: int = REPORT_MAX_BYTES
    manifest_max_bytes: int = _PACKAGE_LIMITS.manifest_max_bytes
    package_max_bytes: int = _PACKAGE_LIMITS.package_max_bytes


@dataclass(frozen=True)
class OutputFile:
    name: str
    path: Path
    size: int
    sha256: bytes  # calculado pelo worker ao ler o arquivo, não declarado pelo executor


@dataclass(frozen=True)
class _Found:
    """Arquivo regular de out/ ainda não lido: nome, caminho e tamanho do `lstat`."""

    name: str
    path: Path
    size: int


@dataclass(frozen=True)
class ReportItem:
    position: int
    accepted: bool
    outputs: tuple[str, ...]
    # Código da rejeição (catálogo de 1.6); `None` para item aceito.
    code: ItemRejectionCode | None
    has_warnings: bool
    # Só na duplicata do Detox: posição da primeira ocorrência, que é um item aceito com saídas.
    duplicate_of: int | None = None


@dataclass(frozen=True)
class ReportSummary:
    """Contagens do relatório, iguais às que os itens dão; vão para `run.summary`."""

    inputs: int
    ok: int
    rejected: int
    duplicates: int
    outputs: int

    @classmethod
    def of(cls, items: tuple[ReportItem, ...]) -> ReportSummary:
        rejected = sum(1 for item in items if not item.accepted)
        duplicates = sum(1 for item in items if item.duplicate_of is not None)
        return cls(
            inputs=len(items),
            ok=len(items) - rejected - duplicates,
            rejected=rejected,
            duplicates=duplicates,
            outputs=sum(len(item.outputs) for item in items),
        )

    def as_json(self) -> dict[str, int]:
        return asdict(self)


@dataclass(frozen=True)
class ParsedReport:
    items: tuple[ReportItem, ...]
    has_global_warnings: bool
    summary: ReportSummary

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
    # Os dois juntos, ou nenhum (executor anterior à fatia 5).
    manifest_file: OutputFile | None = None
    package_file: OutputFile | None = None


DEFAULT_LIMITS = OutputLimits()


def _no_heartbeat() -> None:
    return None


def collect_outputs(
    out_dir: Path,
    input_count: int,
    limits: OutputLimits = DEFAULT_LIMITS,
    *,
    recipe_id: str,
    heartbeat: Callable[[], None] = _no_heartbeat,
) -> Collected:
    """`recipe_id` é o da Run, não o do relatório: dá o único nome aceito para o pacote."""
    package_name = package_name_for(recipe_id)
    files = _scan(out_dir, limits, frozenset({REPORT_NAME, MANIFEST_NAME, package_name}))
    manifest, package = _package_files(
        files.pop(MANIFEST_NAME, None), files.pop(package_name, None), limits
    )
    report_found = files.pop(REPORT_NAME, None)
    if report_found is None:
        raise CollectionError(f"{REPORT_NAME} ausente em out/")
    if report_found.size > limits.report_max_bytes:
        raise OutputLimitError(
            ErrorCode.REPORT_LIMIT_EXCEEDED,
            f"relatório com {report_found.size} bytes; o teto é {limits.report_max_bytes}",
        )
    report_data = _read_no_follow(report_found.path, limits.report_max_bytes)
    report = parse_report(report_data, input_count)
    cited = [name for item in report.items for name in item.outputs]
    if len(set(cited)) != len(cited) or set(cited) != set(files):
        raise CollectionError("os arquivos de out/ não batem com as saídas citadas no relatório")
    outputs = tuple(_digested(files[name], heartbeat) for name in cited)
    report_file = _from_bytes(report_found, report_data)
    if manifest is None or package is None:
        return Collected(outputs, report_file, report)
    manifest_data = _read_no_follow(manifest.path, limits.manifest_max_bytes)
    manifest_file = _from_bytes(manifest, manifest_data)
    package_file = _digested(package, heartbeat)
    try:
        verify_manifest(manifest_data, outputs, _manifest_inputs(report), recipe_id)
        verify_zip(package.path, [*outputs, manifest_file, report_file], heartbeat)
    except PackageError as error:
        raise CollectionError(str(error)) from error
    return Collected(outputs, report_file, report, manifest_file, package_file)


def _manifest_inputs(report: ParsedReport) -> list[ManifestInput]:
    return [
        ManifestInput(
            item.position,
            _status_of(item),
            item.outputs,
            None if item.code is None else item.code.value,
            item.duplicate_of,
        )
        for item in report.items
    ]


def _status_of(item: ReportItem) -> str:
    if not item.accepted:
        return _ITEM_REJECTED
    return _ITEM_OK if item.duplicate_of is None else _ITEM_DUPLICATE


def _digested(found: _Found, heartbeat: Callable[[], None]) -> OutputFile:
    """Lê o arquivo sem seguir symlink, calculando o SHA-256; nunca além do tamanho do `lstat`."""
    digest = hashlib.sha256()
    size = 0
    fd = os.open(found.path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as source:
        while size <= found.size and (chunk := source.read(READ_CHUNK_BYTES)):
            heartbeat()
            size += len(chunk)
            digest.update(chunk)
    if size != found.size:
        raise CollectionError(f"out/ mudou durante a coleta: {found.name!r}")
    return OutputFile(found.name, found.path, size, digest.digest())


def _from_bytes(found: _Found, data: bytes) -> OutputFile:
    return OutputFile(found.name, found.path, len(data), hashlib.sha256(data).digest())


def _package_files(
    manifest: _Found | None, package: _Found | None, limits: OutputLimits
) -> tuple[_Found | None, _Found | None]:
    if (manifest is None) != (package is None):
        raise CollectionError(f"{MANIFEST_NAME} e o pacote vêm juntos em out/, ou nenhum dos dois")
    if manifest is not None and manifest.size > limits.manifest_max_bytes:
        raise OutputLimitError(
            ErrorCode.REPORT_LIMIT_EXCEEDED,
            f"manifesto com {manifest.size} bytes; o teto é {limits.manifest_max_bytes}",
        )
    if package is not None and package.size > limits.package_max_bytes:
        raise OutputLimitError(
            ErrorCode.OUTPUT_LIMIT_EXCEEDED,
            f"pacote com {package.size} bytes; o teto é {limits.package_max_bytes}",
        )
    return manifest, package


def parse_report(data: bytes, input_count: int) -> ParsedReport:
    """Confere o `report.json` contra o schema 1 documentado no pacote do executor."""
    try:
        raw = parse_strict_json(data)
    except ValueError as error:
        raise CollectionError(f"relatório não é JSON aceito: {error}") from None
    document = _mapping(raw, "relatório")
    if document.get("schema_version") != REPORT_SCHEMA_VERSION:
        raise CollectionError("relatório com schema_version desconhecida")
    items = tuple(_item(raw_item) for raw_item in _list(document.get("items"), "items"))
    if [item.position for item in items] != list(range(input_count)):
        raise CollectionError(f"relatório deve ter as posições 0..{input_count - 1}, na ordem")
    _require_duplicates_of_earlier_outputs(items)
    summary = _summary(document.get("summary"), items)
    warnings = _list(document.get("warnings"), "warnings")
    return ParsedReport(items, bool(warnings), summary)


def _summary(raw: object, items: tuple[ReportItem, ...]) -> ReportSummary:
    """O `summary` do relatório só passa se disser exatamente o que os itens dizem."""
    summary = _mapping(raw, "summary")
    if not _SUMMARY_REQUIRED_KEYS <= summary.keys() <= _SUMMARY_KEYS:
        raise CollectionError("summary com chaves fora do schema 1")
    expected = ReportSummary.of(items)
    reported = {key: _int(value, f"summary.{key}") for key, value in summary.items()}
    if {"duplicates": 0} | reported != expected.as_json():
        raise CollectionError("summary não confere com os itens do relatório")
    return expected


def _scan(out_dir: Path, limits: OutputLimits, outside_budget: frozenset[str]) -> dict[str, _Found]:
    """Arquivos de out/ pelo caminho relativo; os nomes de `outside_budget`, só na raiz, não
    contam nos tetos de conteúdo."""
    try:
        info = out_dir.lstat()
    except FileNotFoundError:
        raise CollectionError("out/ ausente") from None
    if not stat.S_ISDIR(info.st_mode):
        raise CollectionError("out/ não é diretório regular")
    budget = _ContentBudget(limits)
    files: dict[str, _Found] = {}
    with os.scandir(out_dir) as entries:
        for entry in entries:
            if entry.is_dir(follow_symlinks=False):
                files.update(_scan_group(entry, budget))
                continue
            output = _regular_file(entry, entry.name)
            files[output.name] = output
            if output.name not in outside_budget:
                budget.spend(output)
    return files


def _scan_group(directory: os.DirEntry[str], budget: _ContentBudget) -> dict[str, _Found]:
    """Um grupo da lista fechada; dentro dele, só arquivos regulares (nenhum segundo nível)."""
    if directory.name not in _GROUPS:
        raise CollectionError(f"out/ contém subpasta fora da lista de grupos: {directory.name!r}")
    files: dict[str, _Found] = {}
    with os.scandir(directory.path) as entries:
        for entry in entries:
            output = _regular_file(entry, f"{directory.name}/{entry.name}")
            files[output.name] = output
            budget.spend(output)
    return files


class _ContentBudget:
    """Tetos de conteúdo, conferidos a cada arquivo: um diretório com milhões de entradas não é
    lido inteiro."""

    def __init__(self, limits: OutputLimits) -> None:
        self._limits = limits
        self._files = 0
        self._bytes = 0

    def spend(self, output: _Found) -> None:
        self._files += 1
        self._bytes += output.size
        if self._files > self._limits.max_files:
            raise OutputLimitError(
                ErrorCode.OUTPUT_LIMIT_EXCEEDED, f"mais de {self._limits.max_files} arquivos"
            )
        if self._bytes > self._limits.max_bytes:
            raise OutputLimitError(
                ErrorCode.OUTPUT_LIMIT_EXCEEDED, f"saídas acima de {self._limits.max_bytes} bytes"
            )


def _regular_file(entry: os.DirEntry[str], name: str) -> _Found:
    _require_output_name(entry.name, name)
    info = entry.stat(follow_symlinks=False)
    if not stat.S_ISREG(info.st_mode):
        raise CollectionError(f"out/ contém algo que não é arquivo regular: {name!r}")
    if info.st_nlink != 1:
        raise CollectionError(f"out/ contém hardlink: {name!r}")
    return _Found(name, Path(entry.path), info.st_size)


def _require_output_name(component: str, name: str) -> None:
    """As regras de 3.6 que o `OutputDir` aplica, conferidas de novo no que o executor deixou."""
    try:
        size = len(component.encode("utf-8"))
    except UnicodeEncodeError:
        raise CollectionError(f"out/ contém nome que não é UTF-8: {name!r}") from None
    if size > MAX_COMPONENT_BYTES or len(name.encode()) > MAX_PATH_BYTES:
        raise CollectionError(f"out/ contém nome longo demais: {name!r}")
    if unicodedata.normalize("NFC", component) != component:
        raise CollectionError(f"out/ contém nome fora de NFC: {name!r}")
    if "\\" in component:
        raise CollectionError(f"out/ contém nome com barra invertida: {name!r}")
    if has_unsafe_char(component):
        raise CollectionError(f"out/ contém nome com caractere de controle ou formato: {name!r}")


def _read_no_follow(path: Path, max_bytes: int) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as source:
        data = source.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise OutputLimitError(
            ErrorCode.REPORT_LIMIT_EXCEEDED, "relatório ou manifesto cresceu além do teto"
        )
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
        return ReportItem(position, False, (), _rejection_code(item.get("code")), False)
    if status == _ITEM_DUPLICATE:
        # Aceito, sem saídas e sem aviso: é o comportamento pedido pelo parâmetro do Detox (1.4).
        duplicate_of = _int(item.get("duplicate_of"), "item.duplicate_of")
        return ReportItem(position, True, (), None, False, duplicate_of)
    raise CollectionError(f"item com status desconhecido: {status!r}")


def _rejection_code(raw: object) -> ItemRejectionCode:
    if not isinstance(raw, str) or raw not in ItemRejectionCode:
        raise CollectionError("item.code fora do catálogo de rejeições")
    return ItemRejectionCode(raw)


def _require_duplicates_of_earlier_outputs(items: tuple[ReportItem, ...]) -> None:
    for item in items:
        if item.duplicate_of is None:
            continue
        original = items[item.duplicate_of] if 0 <= item.duplicate_of < item.position else None
        if original is None or not original.outputs:
            raise CollectionError(
                f"item {item.position}: duplicate_of deve apontar para um item anterior com saídas"
            )


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
