"""Root-owned administrative image policy, reopened securely at each start."""

import json
import os
import re
import stat
from pathlib import Path

# Preserve the immutable reference syntax already accepted by the executor.
IMAGE_REFERENCE = re.compile(r"[a-z0-9][a-z0-9._/:-]{0,254}@sha256:[0-9a-f]{64}")
MAX_ALLOWLIST_BYTES = 64 * 1024


class ImageNotAllowedError(Exception):
    """The image is absent from the policy, or the policy cannot be trusted."""


def require_allowed(images_file: Path, image: str) -> None:
    if image not in _read_allowlist(images_file):
        raise ImageNotAllowedError("imagem fora da allowlist do executor")


def _read_root_file(path: Path, *, private: bool = False) -> bytes:
    """Read one bounded root file through verified, descriptor-anchored ancestry.

    Also used by the installer's read-only configuration preflight. No repair,
    directory creation, legacy fallback, or ownership promotion occurs here.
    """
    if not path.is_absolute() or ".." in path.parts or len(path.parts) < 2:
        raise ImageNotAllowedError("caminho administrativo precisa ser absoluto e canônico")
    directory_fd = None
    try:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        directory_fd = os.open("/", flags)
        _require_root_metadata(os.fstat(directory_fd), directory=True)
        for component in path.parts[1:-1]:
            child_fd = os.open(component, flags, dir_fd=directory_fd)
            try:
                _require_root_metadata(os.fstat(child_fd), directory=True)
            except BaseException:
                os.close(child_fd)
                raise
            os.close(directory_fd)
            directory_fd = child_fd
        # NONBLOCK prevents a malicious FIFO from hanging the reader before fstat.
        leaf_fd = os.open(
            path.name,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
            dir_fd=directory_fd,
        )
        with os.fdopen(leaf_fd, "rb") as file:
            metadata = os.fstat(file.fileno())
            _require_root_metadata(metadata, directory=False)
            if private and stat.S_IMODE(metadata.st_mode) != 0o600:
                raise ImageNotAllowedError("política administrativa exige modo 0600")
            if metadata.st_size > MAX_ALLOWLIST_BYTES:
                raise ImageNotAllowedError("arquivo administrativo acima do tamanho máximo")
            raw = file.read(MAX_ALLOWLIST_BYTES + 1)
    except OSError as error:
        raise ImageNotAllowedError(
            f"arquivo administrativo ilegível ({type(error).__name__})"
        ) from error
    finally:
        if directory_fd is not None:
            os.close(directory_fd)
    if len(raw) > MAX_ALLOWLIST_BYTES:
        raise ImageNotAllowedError("arquivo administrativo acima do tamanho máximo")
    return raw


def _require_root_metadata(metadata: os.stat_result, *, directory: bool) -> None:
    correct_type = stat.S_ISDIR(metadata.st_mode) if directory else stat.S_ISREG(metadata.st_mode)
    if not correct_type or metadata.st_uid != 0 or metadata.st_gid != 0 or metadata.st_mode & 0o022:
        raise ImageNotAllowedError(
            "cadeia administrativa precisa pertencer ao root sem escrita externa"
        )
    if not directory and metadata.st_nlink != 1:
        raise ImageNotAllowedError("arquivo administrativo precisa ter um único link")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("chave JSON duplicada")
        result[key] = value
    return result


def _read_allowlist(images_file: Path) -> frozenset[str]:
    raw = _read_root_file(images_file, private=True)
    try:
        content = json.loads(raw, object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, ValueError, RecursionError) as error:
        raise ImageNotAllowedError("allowlist não é JSON válido e inequívoco") from error
    allowed = content.get("allowed") if isinstance(content, dict) else None
    if (
        not isinstance(content, dict)
        or set(content) != {"allowed"}
        or not isinstance(allowed, list)
    ):
        raise ImageNotAllowedError('allowlist precisa ser {"allowed": ["<ref>@sha256:<hex>"]}')
    if not all(isinstance(entry, str) and IMAGE_REFERENCE.fullmatch(entry) for entry in allowed):
        raise ImageNotAllowedError("allowlist exige referências imutáveis válidas")
    return frozenset(allowed)
