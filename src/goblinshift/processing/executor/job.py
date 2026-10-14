"""Leitura estrita de `job.json` e conferência das entradas antes de processar.

O job vem do worker, que é confiável, mas atravessa a fronteira do sandbox: qualquer desvio do
contrato é tratado como defeito de infraestrutura, nunca adivinhado.
"""

import hashlib
import json
import os
import re
import stat
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from goblinshift.processing.recipe import InputFile

MIB = 1024 * 1024
JOB_MAX_BYTES = 1 * MIB
CONTEXT_MAX_BYTES = 64 * 1024
HASH_CHUNK_BYTES = 1 * MIB
_SHA256_HEX = re.compile(r"[0-9a-f]{64}")
_JOB_KEYS = frozenset({"recipe_id", "recipe_version", "parameters", "inputs"})
_INPUT_KEYS = frozenset({"position", "path", "original_name", "size", "sha256"})


class JobError(Exception):
    """`job.json` fora do contrato documentado no pacote do executor."""


class InputIntegrityError(Exception):
    """Entrada ausente, que não é arquivo regular, ou diferente do que o job declara."""


@dataclass(frozen=True)
class InputEntry:
    position: int
    path: str  # um componente, relativo a work_dir/inputs/
    original_name: str
    size: int
    sha256: str


@dataclass(frozen=True)
class Job:
    recipe_id: str
    recipe_version: int
    parameters: Mapping[str, object]
    inputs: tuple[InputEntry, ...]


def read_job(path: Path) -> Job:
    try:
        with path.open("rb") as source:
            data = source.read(JOB_MAX_BYTES + 1)
    except OSError as error:
        raise JobError(f"job ilegível: {error}") from error
    if len(data) > JOB_MAX_BYTES:
        raise JobError(f"job passa de {JOB_MAX_BYTES} bytes")
    job = _object(_parsed(data, "job"), _JOB_KEYS, "job")
    return Job(
        recipe_id=_text(job["recipe_id"], "recipe_id"),
        recipe_version=_integer(job["recipe_version"], "recipe_version"),
        parameters=_object(job["parameters"], None, "parameters"),
        inputs=_entries(job["inputs"]),
    )


@dataclass(frozen=True)
class ExecutionContext:
    """Fatos da execução que não mudam o processamento, só o que o manifesto registra."""

    # Imagem fixada na Run ("<repo>@sha256:<hex>", ou "local-dev" fora de produção). None quando
    # o worker ainda não escreve o context.json, na janela de deploy (ADR 020).
    executor_image: str | None


def read_context(path: Path) -> ExecutionContext:
    """Lê o `context.json` opcional ao lado do `job.json` (ADR 020).

    Ausente é a janela de deploy (worker anterior à fatia 5), não erro. Presente, é lido como o
    job, com uma diferença: chaves desconhecidas são ignoradas (tolerant reader), para um worker
    mais novo poder acrescentar campos sem derrubar as Runs fixadas numa imagem anterior.
    """
    try:
        # O_NOFOLLOW: o arquivo é do worker, e um symlink ali nunca é seguido.
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return ExecutionContext(executor_image=None)
    except OSError as error:
        raise JobError(f"context.json ilegível: {error}") from error
    with os.fdopen(fd, "rb") as source:
        data = source.read(CONTEXT_MAX_BYTES + 1)
    if len(data) > CONTEXT_MAX_BYTES:
        raise JobError(f"context.json passa de {CONTEXT_MAX_BYTES} bytes")
    context = _object(_parsed(data, "context.json"), None, "context.json")
    if "executor_image" not in context:
        raise JobError("context.json sem executor_image")
    return ExecutionContext(executor_image=_image(context["executor_image"]))


def _image(raw: object) -> str | None:
    if raw is None:
        return None
    image = _text(raw, "executor_image")
    if not image or any(char < " " or char == "\x7f" for char in image):
        raise JobError("executor_image deve ser texto não vazio, sem caracteres de controle")
    return image


def verified_inputs(inputs_dir: Path, entries: Sequence[InputEntry]) -> list[InputFile]:
    """Confere tipo, tamanho e SHA-256 de cada entrada; adulteração impede a execução inteira."""
    return [_verified(inputs_dir / entry.path, entry) for entry in entries]


def _parsed(data: bytes, where: str) -> object:
    try:
        return json.loads(data, object_pairs_hook=_reject_duplicates, parse_constant=_reject)
    except ValueError as error:
        raise JobError(f"{where} não é JSON aceito: {error}") from error


def _entries(raw: object) -> tuple[InputEntry, ...]:
    if not isinstance(raw, list):
        raise JobError("inputs deve ser uma lista")
    entries = tuple(_entry(item) for item in raw)
    positions = [entry.position for entry in entries]
    if positions != list(range(len(entries))):
        raise JobError(f"posições devem ser 0, 1, 2… na ordem; vieram {positions}")
    return entries


def _entry(raw: object) -> InputEntry:
    entry = _object(raw, _INPUT_KEYS, "inputs[]")
    path = _text(entry["path"], "inputs[].path")
    if path in ("", ".", "..") or "/" in path or "\\" in path or "\x00" in path:
        raise JobError(f"inputs[].path deve ser um nome dentro de inputs/: {path!r}")
    sha256 = _text(entry["sha256"], "inputs[].sha256")
    if not _SHA256_HEX.fullmatch(sha256):
        raise JobError("inputs[].sha256 deve ter 64 dígitos hexadecimais minúsculos")
    size = _integer(entry["size"], "inputs[].size")
    if size < 0:
        raise JobError("inputs[].size não pode ser negativo")
    return InputEntry(
        position=_integer(entry["position"], "inputs[].position"),
        path=path,
        original_name=_text(entry["original_name"], "inputs[].original_name"),
        size=size,
        sha256=sha256,
    )


def _verified(path: Path, entry: InputEntry) -> InputFile:
    try:
        # lstat: um symlink plantado nunca é seguido para fora de inputs/.
        status = path.lstat()
    except FileNotFoundError:
        raise InputIntegrityError(f"entrada {entry.position} ausente") from None
    if not stat.S_ISREG(status.st_mode):
        raise InputIntegrityError(f"entrada {entry.position} não é arquivo regular")
    if status.st_size != entry.size:
        raise InputIntegrityError(
            f"entrada {entry.position} tem {status.st_size} bytes; o job declara {entry.size}"
        )
    if _sha256(path) != entry.sha256:
        raise InputIntegrityError(f"entrada {entry.position} com SHA-256 diferente do job")
    return InputFile(entry.position, path, entry.original_name, entry.size, entry.sha256)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(HASH_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _object(raw: object, keys: frozenset[str] | None, where: str) -> dict[str, object]:
    """Objeto JSON; com `keys`, exatamente esses campos."""
    if not isinstance(raw, dict):
        raise JobError(f"{where} deve ser um objeto")
    if keys is not None and set(raw) != keys:
        missing = sorted(keys - set(raw))
        unknown = sorted(set(raw) - keys)
        raise JobError(f"{where}: faltando {missing}, desconhecidos {unknown}")
    return raw


def _text(raw: object, where: str) -> str:
    if not isinstance(raw, str):
        raise JobError(f"{where} deve ser texto")
    return raw


def _integer(raw: object, where: str) -> int:
    # bool é subclasse de int em Python, mas `true` não é número no JSON.
    if not isinstance(raw, int) or isinstance(raw, bool):
        raise JobError(f"{where} deve ser inteiro")
    return raw


def _reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    keys = [key for key, _ in pairs]
    repeated = sorted({key for key in keys if keys.count(key) > 1})
    if repeated:
        raise ValueError(f"chaves repetidas: {repeated}")
    return dict(pairs)


def _reject(name: str) -> object:
    raise ValueError(f"{name} não é um valor JSON aceito")
