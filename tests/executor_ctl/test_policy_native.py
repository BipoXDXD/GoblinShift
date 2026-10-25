"""Linux/root acceptance SOURCE: execute only in a dedicated disposable runner.

No metadata simulation. Ordinary Mac/non-root collection skips every case; those
skips are not native evidence. All changes stay in a private /root directory.
"""

import json
import os
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from executor_ctl.images import ImageNotAllowedError, require_allowed

pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or os.geteuid() != 0,
    reason="requires disposable Linux runner as root; native acceptance still pending",
)
IMAGE = "ghcr.io/bipoxdxd/goblinshift-executor@sha256:" + "b" * 64
PREVIOUS = "ghcr.io/bipoxdxd/goblinshift-executor@sha256:" + "c" * 64


@pytest.fixture
def native_policy() -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="gs-policy-", dir="/root") as directory:
        parent = Path(directory) / "goblinshift"
        parent.mkdir(mode=0o755)
        policy = parent / "executor-images.json"
        policy.write_text(json.dumps({"allowed": [PREVIOUS, IMAGE]}))
        policy.chmod(0o600)
        yield policy


def test_real_root_policy_preserves_both_approved_digests(native_policy: Path) -> None:
    require_allowed(native_policy, PREVIOUS)
    require_allowed(native_policy, IMAGE)


@pytest.mark.parametrize(
    "case",
    [
        "parent_uid1001",
        "parent_gid1001",
        "parent_group_write",
        "parent_symlink",
        "leaf_uid1001",
        "leaf_gid1001",
        "leaf_not_private",
        "leaf_group_write",
        "leaf_hardlink",
        "leaf_symlink",
        "leaf_fifo",
    ],
)
def test_real_linux_unsafe_policy_is_refused(native_policy: Path, case: str) -> None:
    if case == "parent_uid1001":
        os.chown(native_policy.parent, 1001, 1001)
    elif case == "parent_gid1001":
        os.chown(native_policy.parent, 0, 1001)
    elif case == "parent_group_write":
        native_policy.parent.chmod(0o775)
    elif case == "parent_symlink":
        renamed = native_policy.parent.with_name("original")
        native_policy.parent.rename(renamed)
        native_policy.parent.symlink_to(renamed, target_is_directory=True)
    elif case == "leaf_uid1001":
        os.chown(native_policy, 1001, 1001)
    elif case == "leaf_gid1001":
        os.chown(native_policy, 0, 1001)
    elif case == "leaf_not_private":
        native_policy.chmod(0o644)
    elif case == "leaf_group_write":
        native_policy.chmod(0o620)
    elif case == "leaf_hardlink":
        os.link(native_policy, native_policy.with_name("other.json"))
    elif case == "leaf_symlink":
        original = native_policy.with_name("original.json")
        native_policy.rename(original)
        native_policy.symlink_to(original)
    elif case == "leaf_fifo":
        native_policy.unlink()
        os.mkfifo(native_policy, 0o600)
    with pytest.raises(ImageNotAllowedError):
        require_allowed(native_policy, IMAGE)


def test_real_linux_atomic_replacement_is_reread(native_policy: Path) -> None:
    require_allowed(native_policy, PREVIOUS)
    replacement = native_policy.with_name("replacement.json")
    replacement.write_text(json.dumps({"allowed": [IMAGE]}))
    replacement.chmod(0o600)
    replacement.replace(native_policy)
    with pytest.raises(ImageNotAllowedError):
        require_allowed(native_policy, PREVIOUS)
    require_allowed(native_policy, IMAGE)
