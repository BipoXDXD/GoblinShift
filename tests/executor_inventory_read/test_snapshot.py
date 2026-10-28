import hashlib
import json
import os
import threading
from pathlib import Path

import pytest

from executor_ctl import config, images

from .conftest import MetadataBoundary

A = "example.invalid/executor@sha256:" + "a" * 64
B = "example.invalid/executor@sha256:" + "b" * 64


def policy(tmp_path: Path, raw: bytes | None = None) -> Path:
    path = (tmp_path / "policy.json").resolve()
    path.write_bytes(raw if raw is not None else json.dumps({"allowed": [A, B]}).encode())
    path.chmod(0o600)
    return path


def test_allowed_returns_same_bytes_snapshot_and_real_fd_chain(
    tmp_path: Path, root_metadata_boundary: MetadataBoundary
) -> None:
    raw = (' { "allowed" : ["' + A + '","' + B + '"] }\n').encode()
    path = policy(tmp_path, raw)
    result = images.require_allowed(path, A)
    assert result is not None, "allowed membership must return the consumed-policy snapshot"
    assert result.bytes_sha256 == hashlib.sha256(raw).hexdigest()
    assert result.byte_count == len(raw)
    assert [hop.component for hop in result.fd_chain] == list(path.parts)
    assert result.before.dev == os.stat(path).st_dev
    assert result.before.ino == os.stat(path).st_ino
    assert result.before == result.after
    assert result.fd_chain[-1].before == result.before
    assert result.fd_chain[-1].after == result.after
    for parent, child in zip(result.fd_chain, result.fd_chain[1:], strict=False):
        assert (child.parent_dev, child.parent_ino) == (parent.before.dev, parent.before.ino)


def test_root_file_bytes_api_remains_unchanged(
    tmp_path: Path, root_metadata_boundary: MetadataBoundary
) -> None:
    path = policy(tmp_path)
    assert images._read_root_file(path, private=True) == path.read_bytes()


def test_reopening_after_atomic_replace_observes_new_policy(
    tmp_path: Path, root_metadata_boundary: MetadataBoundary
) -> None:
    path = policy(tmp_path, json.dumps({"allowed": [A]}).encode())
    first = images.require_allowed(path, A)
    replacement = tmp_path / "next.json"
    replacement.write_bytes(json.dumps({"allowed": [B]}).encode())
    replacement.chmod(0o600)
    os.replace(replacement, path)
    second = images.require_allowed(path, B)
    assert first is not None and second is not None
    assert first.bytes_sha256 != second.bytes_sha256
    assert first.before.ino != second.before.ino
    with pytest.raises(images.ImageNotAllowedError):
        images.require_allowed(path, A)


def test_mutation_between_pre_and_post_fstat_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, root_metadata_boundary: MetadataBoundary
) -> None:
    path = policy(tmp_path)
    start = threading.Event()
    done = threading.Event()
    triggered = False

    def mutate() -> None:
        assert start.wait(2)
        path.write_bytes(json.dumps({"allowed": [A]}).encode())
        done.set()

    thread = threading.Thread(target=mutate)
    thread.start()

    def boundary(metadata: os.stat_result, *, directory: bool) -> None:
        nonlocal triggered
        root_metadata_boundary(metadata, directory=directory)
        if not directory and not triggered:
            triggered = True
            start.set()
            assert done.wait(2)

    monkeypatch.setattr(images, "_require_root_metadata", boundary)
    try:
        with pytest.raises(images.ImageNotAllowedError):
            images.require_allowed(path, A)
    finally:
        thread.join(3)
    assert not thread.is_alive()


def test_config_hash_is_the_raw_buffer_used_by_parser(
    tmp_path: Path, root_metadata_boundary: MetadataBoundary
) -> None:
    path = (tmp_path / "config.json").resolve()
    content = {
        "socket": "/run/test.sock",
        "group": "test",
        "work_area": "/work/test",
        "runtime": "runsc",
        "images_file": config.IMAGES_FILE,
    }
    raw = ("  " + json.dumps(content) + "\n").encode()
    path.write_bytes(raw)
    path.chmod(0o600)
    loaded = config.load_config(path)
    assert getattr(loaded, "loaded_bytes_sha256", None) == hashlib.sha256(raw).hexdigest()
    plain = config.Config(
        loaded.socket, loaded.group, loaded.work_area, loaded.runtime, loaded.images_file
    )
    assert loaded == plain
    assert getattr(plain, "loaded_bytes_sha256", None) is None


@pytest.mark.parametrize(
    "raw",
    [
        b'{"allowed":[],"allowed":[]}',
        b'{"allowed":["latest"]}',
        b'{"allowed":[],"extra":1}',
        b"[]",
        b"not-json",
    ],
)
def test_invalid_policy_never_has_positive_snapshot(
    tmp_path: Path, root_metadata_boundary: MetadataBoundary, raw: bytes
) -> None:
    with pytest.raises(images.ImageNotAllowedError):
        images.require_allowed(policy(tmp_path, raw), A)


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo", "ancestor_symlink"])
def test_real_posix_unsafe_objects_are_rejected(
    tmp_path: Path, root_metadata_boundary: MetadataBoundary, kind: str
) -> None:
    path = policy(tmp_path)
    if kind == "symlink":
        link = tmp_path / "link"
        link.symlink_to(path)
        path = link
    elif kind == "hardlink":
        os.link(path, tmp_path / "second-link")
    elif kind == "fifo":
        path.unlink()
        os.mkfifo(path)
    else:
        directory = tmp_path / "real"
        directory.mkdir()
        path.rename(directory / "policy.json")
        link = tmp_path / "linked"
        link.symlink_to(directory, target_is_directory=True)
        path = link / "policy.json"
    with pytest.raises(images.ImageNotAllowedError):
        images.require_allowed(path, A)


@pytest.mark.parametrize("mutation", ["replace", "mode", "directory_mode"])
def test_detects_changed_descriptor_authority_during_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    root_metadata_boundary: MetadataBoundary,
    mutation: str,
) -> None:
    path = policy(tmp_path)
    changed = False

    def boundary(metadata: os.stat_result, *, directory: bool) -> None:
        nonlocal changed
        root_metadata_boundary(metadata, directory=directory)
        if not directory and not changed:
            changed = True
            if mutation == "mode":
                path.chmod(0o640)
            elif mutation == "directory_mode":
                path.parent.chmod(0o750)
            else:
                replacement = tmp_path / "next"
                replacement.write_bytes(json.dumps({"allowed": [A]}).encode())
                replacement.chmod(0o600)
                os.replace(replacement, path)

    monkeypatch.setattr(images, "_require_root_metadata", boundary)
    with pytest.raises(images.ImageNotAllowedError):
        images.require_allowed(path, A)


def test_descriptor_cleanup_after_authority_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, root_metadata_boundary: MetadataBoundary
) -> None:
    path = policy(tmp_path)
    probe = os.open(path, os.O_RDONLY)
    os.close(probe)

    def fail(metadata: os.stat_result, *, directory: bool) -> None:
        if not directory:
            raise images.ImageNotAllowedError("synthetic authority failure")

    monkeypatch.setattr(images, "_require_root_metadata", fail)
    with pytest.raises(images.ImageNotAllowedError):
        images.require_allowed(path, A)
    after = os.open(path, os.O_RDONLY)
    try:
        assert after == probe
    finally:
        os.close(after)
