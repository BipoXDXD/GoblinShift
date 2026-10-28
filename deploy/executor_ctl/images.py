"""Root-owned administrative image policy, reopened securely at each start."""

import hashlib
import json
import os
import re
import stat
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path

# Preserve the immutable reference syntax already accepted by the executor.
IMAGE_REFERENCE = re.compile(r"[a-z0-9][a-z0-9._/:-]{0,254}@sha256:[0-9a-f]{64}")
MAX_ALLOWLIST_BYTES = 64 * 1024


class ImageNotAllowedError(Exception):
    """The image is absent from the policy, or the policy cannot be trusted."""


@dataclass(frozen=True)
class FileMetadata:
    dev: int
    ino: int
    mode: int
    uid: int
    gid: int
    nlink: int
    size: int
    mtime_ns: int
    ctime_ns: int

    @classmethod
    def capture(cls, metadata: os.stat_result) -> "FileMetadata":
        return cls(
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_mode,
            metadata.st_uid,
            metadata.st_gid,
            metadata.st_nlink,
            metadata.st_size,
            metadata.st_mtime_ns,
            metadata.st_ctime_ns,
        )

    def directory_guard(self) -> tuple[int, int, int, int, int]:
        # Unrelated directory entries can change size/link count/timestamps.
        return self.dev, self.ino, self.mode, self.uid, self.gid


@dataclass(frozen=True)
class DescriptorHop:
    component: str
    parent_dev: int | None
    parent_ino: int | None
    before: FileMetadata
    after: FileMetadata


@dataclass(frozen=True)
class FileSnapshot:
    path: str
    bytes_sha256: str
    byte_count: int
    fd_chain: tuple[DescriptorHop, ...]
    before: FileMetadata
    after: FileMetadata


@dataclass(frozen=True)
class PolicyRead(FileSnapshot):
    requested_image_sha256: str


@dataclass(frozen=True)
class _ReadBuffer:
    raw: bytes = field(repr=False)
    snapshot: FileSnapshot


def require_allowed(images_file: Path, image: str) -> PolicyRead:
    read = _read_root_snapshot(images_file, private=True)
    if image not in _parse_allowlist(read.raw):
        raise ImageNotAllowedError("imagem fora da allowlist do executor")
    snapshot = read.snapshot
    return PolicyRead(
        snapshot.path,
        snapshot.bytes_sha256,
        snapshot.byte_count,
        snapshot.fd_chain,
        snapshot.before,
        snapshot.after,
        hashlib.sha256(image.encode("utf-8")).hexdigest(),
    )


def _read_root_file(path: Path, *, private: bool = False) -> bytes:
    """Preserve the bounded bytes API used by config and the installer preflight."""
    return _read_root_snapshot(path, private=private).raw


def _read_root_snapshot(path: Path, *, private: bool = False) -> _ReadBuffer:
    """Capture bytes and fstat ancestry through the SAME open descriptors.

    No pathname reopen, process discovery, side-channel logging, or repair. The
    snapshot identifies the decision's read, not all future pathname contents.
    """
    if not path.is_absolute() or ".." in path.parts or len(path.parts) < 2:
        raise ImageNotAllowedError("caminho administrativo precisa ser absoluto e canônico")
    directories: list[tuple[int, str, FileMetadata]] = []
    owned_descriptors = ExitStack()
    try:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        root_fd = os.open("/", flags)
        owned_descriptors.callback(os.close, root_fd)
        directories.append((root_fd, "/", FileMetadata.capture(os.fstat(root_fd))))
        _require_root_metadata(os.fstat(root_fd), directory=True)
        for component in path.parts[1:-1]:
            child_fd = os.open(component, flags, dir_fd=directories[-1][0])
            owned_descriptors.callback(os.close, child_fd)
            child_stat = os.fstat(child_fd)
            directories.append((child_fd, component, FileMetadata.capture(child_stat)))
            _require_root_metadata(child_stat, directory=True)
        leaf_fd = os.open(
            path.name,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
            dir_fd=directories[-1][0],
        )
        owned_descriptors.callback(os.close, leaf_fd)
        # ExitStack owns the FD even if fdopen itself fails. The wrapper owns
        # only its buffer, avoiding double-close or close-after-FD-reuse.
        with os.fdopen(leaf_fd, "rb", closefd=False) as file:
            before_stat = os.fstat(file.fileno())
            before = FileMetadata.capture(before_stat)
            _require_root_metadata(before_stat, directory=False)
            if private and stat.S_IMODE(before.mode) != 0o600:
                raise ImageNotAllowedError("política administrativa exige modo 0600")
            if before.size > MAX_ALLOWLIST_BYTES:
                raise ImageNotAllowedError("arquivo administrativo acima do tamanho máximo")
            raw = file.read(MAX_ALLOWLIST_BYTES + 1)
            after_stat = os.fstat(file.fileno())
            after = FileMetadata.capture(after_stat)
            _require_root_metadata(after_stat, directory=False)
            if before != after or len(raw) != before.size or len(raw) > MAX_ALLOWLIST_BYTES:
                raise ImageNotAllowedError("arquivo administrativo mudou durante a leitura")
            chain: list[DescriptorHop] = []
            parent: FileMetadata | None = None
            for fd, component, entry in directories:
                current_stat = os.fstat(fd)
                current = FileMetadata.capture(current_stat)
                _require_root_metadata(current_stat, directory=True)
                if entry.directory_guard() != current.directory_guard():
                    raise ImageNotAllowedError("cadeia administrativa mudou durante a leitura")
                chain.append(
                    DescriptorHop(
                        component,
                        parent.dev if parent else None,
                        parent.ino if parent else None,
                        entry,
                        current,
                    )
                )
                parent = entry
            chain.append(
                DescriptorHop(
                    path.name, directories[-1][2].dev, directories[-1][2].ino, before, after
                )
            )
            snapshot = FileSnapshot(
                str(path), hashlib.sha256(raw).hexdigest(), len(raw), tuple(chain), before, after
            )
            return _ReadBuffer(raw, snapshot)
    except OSError as error:
        raise ImageNotAllowedError(
            f"arquivo administrativo ilegível ({type(error).__name__})"
        ) from error
    finally:
        owned_descriptors.close()


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
    return _parse_allowlist(_read_root_file(images_file, private=True))


def _parse_allowlist(raw: bytes) -> frozenset[str]:
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
