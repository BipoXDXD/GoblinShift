"""Fault only fstat/fdopen; descriptor acquisition/release remains real POSIX I/O."""

import errno
import os
from collections.abc import Callable
from pathlib import Path
from typing import NoReturn

import pytest

from executor_ctl import images

from .conftest import MetadataBoundary
from .test_snapshot import A, policy


def assert_closed_and_cleanup(
    descriptors: set[int], real_fstat: Callable[[int], os.stat_result]
) -> None:
    try:
        for descriptor in descriptors:
            with pytest.raises(OSError) as error:
                real_fstat(descriptor)
            assert error.value.errno == errno.EBADF
    finally:
        # A RED run must clean up any descriptor the implementation leaked.
        for descriptor in descriptors:
            try:
                real_fstat(descriptor)
            except OSError:
                continue
            os.close(descriptor)


@pytest.mark.parametrize("target", ["root", "child"])
def test_first_fstat_failure_still_closes_open_directory(
    tmp_path: Path,
    root_metadata_boundary: MetadataBoundary,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
) -> None:
    path = policy(tmp_path)
    real_fstat = os.fstat
    descriptors: set[int] = set()
    root_descriptor: int | None = None
    failed = False

    def fault(descriptor: int) -> os.stat_result:
        nonlocal root_descriptor, failed
        descriptors.add(descriptor)
        if root_descriptor is None:
            root_descriptor = descriptor
        is_target = (
            descriptor == root_descriptor if target == "root" else descriptor != root_descriptor
        )
        if is_target and not failed:
            failed = True
            raise OSError(errno.EIO, "synthetic first-fstat failure")
        return real_fstat(descriptor)

    with monkeypatch.context() as scoped:
        scoped.setattr(os, "fstat", fault)
        with pytest.raises(images.ImageNotAllowedError):
            images.require_allowed(path, A)
    assert failed
    assert_closed_and_cleanup(descriptors, real_fstat)


def test_fdopen_failure_still_closes_open_leaf(
    tmp_path: Path,
    root_metadata_boundary: MetadataBoundary,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = policy(tmp_path)
    real_fstat = os.fstat
    descriptors: set[int] = set()

    def fail_fdopen(descriptor: int, *args: object, **kwargs: object) -> NoReturn:
        descriptors.add(descriptor)
        raise OSError(errno.EIO, "synthetic fdopen failure")

    with monkeypatch.context() as scoped:
        scoped.setattr(os, "fdopen", fail_fdopen)
        with pytest.raises(images.ImageNotAllowedError):
            images.require_allowed(path, A)
    assert len(descriptors) == 1
    assert_closed_and_cleanup(descriptors, real_fstat)
