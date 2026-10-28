"""Real POSIX I/O, with only the root-metadata authority boundary synthetic."""

import os
import stat
from typing import Protocol

import pytest

from executor_ctl import images


class MetadataBoundary(Protocol):
    def __call__(self, metadata: os.stat_result, *, directory: bool) -> None: ...


@pytest.fixture
def root_metadata_boundary(monkeypatch: pytest.MonkeyPatch) -> MetadataBoundary:
    def synthetic_authority(metadata: os.stat_result, *, directory: bool) -> None:
        kind_ok = stat.S_ISDIR(metadata.st_mode) if directory else stat.S_ISREG(metadata.st_mode)
        if not kind_ok or (not directory and metadata.st_nlink != 1):
            raise images.ImageNotAllowedError("synthetic root boundary: type/link rejected")
        # Ownership and ancestor write bits are synthetic ONLY in this fixture.
        # Actual uid/gid/mode remain unchanged in fstat and in the resulting evidence.

    monkeypatch.setattr(images, "_require_root_metadata", synthetic_authority)
    return synthetic_authority
