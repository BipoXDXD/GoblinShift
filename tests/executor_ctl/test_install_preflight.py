"""Run only temporary installer copies; synthetic filesystem, no sudo or services."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
POLICY = "/var/lib/coolify-release/goblinshift/executor-images.json"
IMAGE = "ghcr.io/bipoxdxd/goblinshift-executor@sha256:" + "b" * 64


@pytest.mark.parametrize(
    "case",
    [
        "config_missing",
        "config_malformed",
        "config_unknown",
        "config_untrusted",
        "config_legacy",
        "config_duplicate",
        "policy_not_private",
        "policy_missing",
        "policy_malformed",
        "policy_empty",
        "policy_duplicate",
        "policy_symlink_parent",
        "policy_untrusted_parent",
    ],
)
def test_installer_refuses_before_first_mutation(tmp_path: Path, case: str) -> None:
    result, effects = run_installer_copy(tmp_path, case)
    assert result.returncode != 0
    assert not effects.exists(), (
        case,
        effects.read_text() if effects.exists() else "",
        result.stderr,
    )
    assert "migra" in result.stderr.lower(), result.stderr


def test_installer_valid_preflight_reaches_first_mutation_only(tmp_path: Path) -> None:
    result, effects = run_installer_copy(tmp_path, "valid")
    assert result.returncode == 91, result.stderr
    assert effects.read_text().strip() == "groupadd"


def run_installer_copy(tmp_path: Path, case: str) -> tuple[subprocess.CompletedProcess[str], Path]:
    checkout = tmp_path / "checkout"
    scripts = checkout / "deploy/executor-ctl"
    scripts.mkdir(parents=True)
    shutil.copytree(
        ROOT / "deploy/executor_ctl",
        checkout / "deploy/executor_ctl",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    script = scripts / "install.sh"
    script.write_text(
        (ROOT / "deploy/executor-ctl/install.sh")
        .read_text()
        .replace("[[ $EUID -eq 0 ]]", "[[ 0 -eq 0 ]]")
    )
    virtual = tmp_path / "root"
    config = virtual / "etc/goblinshift/executor-ctl.json"
    policy = virtual / POLICY.lstrip("/")
    config.parent.mkdir(parents=True)
    policy.parent.mkdir(parents=True)
    content = {
        "socket": "/run/goblinshift/executor-ctl.sock",
        "group": "gs-exec",
        "work_area": "/srv/goblinshift/exec-work",
        "runtime": "runsc",
        "images_file": POLICY,
    }
    config.write_text(json.dumps(content))
    policy.write_text(json.dumps({"allowed": [IMAGE]}))
    policy.chmod(0o600)
    if case == "config_missing":
        config.unlink()
    elif case == "config_malformed":
        config.write_text("{")
    elif case == "config_unknown":
        config.write_text(json.dumps({**content, "extra": True}))
    elif case == "config_legacy":
        config.write_text(
            json.dumps({**content, "images_file": "/srv/goblinshift/executor/images.json"})
        )
    elif case == "config_duplicate":
        config.write_text(json.dumps(content)[:-1] + ', "runtime": "runc"}')
    elif case == "policy_not_private":
        policy.chmod(0o644)
    elif case == "policy_missing":
        policy.unlink()
    elif case == "policy_malformed":
        policy.write_text('{"allowed": ["latest"]}')
    elif case == "policy_empty":
        policy.write_text('{"allowed": []}')
    elif case == "policy_duplicate":
        policy.write_text('{"allowed": [], "allowed": ["' + IMAGE + '"]}')
    elif case == "policy_symlink_parent":
        renamed = policy.parent.with_name("real")
        policy.parent.rename(renamed)
        policy.parent.symlink_to(renamed)

    binaries = tmp_path / "bin"
    binaries.mkdir()
    effects = tmp_path / "mutations"
    for name in (
        "groupadd",
        "usermod",
        "install",
        "systemctl",
        "chown",
        "chmod",
        "rm",
        "mv",
        "mktemp",
    ):
        stub = binaries / name
        stub.write_text(f'#!/bin/sh\necho {name} >> "$MUTATIONS"\nexit 91\n')
        stub.chmod(0o755)
    getent = binaries / "getent"
    getent.write_text("#!/bin/sh\nexit 1\n")
    getent.chmod(0o755)
    # The Python executable boundary maps '/' to a private fixture root and simulates UID0.
    # Real descriptor-relative opens, nofollow flags, parsing and installer order still execute.
    python = binaries / "python3"
    python.write_text(
        f"#!{sys.executable}\n"
        + """import os, sys
from types import SimpleNamespace
sys.dont_write_bytecode = "-B" in sys.argv
source, config = sys.argv[-2:]
sys.path.insert(0, os.path.dirname(source))
from executor_ctl import images
opened = {}
real_open = os.open

def fixture_open(path, flags, *args, **kwargs):
    fd = real_open(os.environ["VIRTUAL_ROOT"] if path == "/" else path, flags, *args, **kwargs)
    opened[fd] = str(path)
    return fd

def fixture_stat(fd):
    fields = list(os.fstat(fd))
    untrusted = (os.environ["CASE"] == "policy_untrusted_parent"
                 and opened.get(fd) == "coolify-release")
    untrusted_config = (os.environ["CASE"] == "config_untrusted"
                        and opened.get(fd) == "executor-ctl.json")
    fields[4] = 1001 if untrusted or untrusted_config else 0
    fields[5] = 0
    fields[0] &= ~0o022
    return os.stat_result(fields)

proxy = {name: getattr(os, name) for name in dir(os)}
proxy.update(open=fixture_open, fstat=fixture_stat)
images.os = SimpleNamespace(**proxy)
sys.argv = ["-", source, config]
exec(compile(sys.stdin.read(), "<installer-preflight>", "exec"))
"""
    )
    python.chmod(0o755)
    env = {
        **os.environ,
        "PATH": str(binaries) + ":/usr/bin:/bin",
        "MUTATIONS": str(effects),
        "VIRTUAL_ROOT": str(virtual),
        "CASE": case,
    }
    before = fixture_snapshot(virtual)
    before_checkout = fixture_snapshot(checkout)
    result = subprocess.run(
        ["/bin/bash", str(script)], env=env, text=True, capture_output=True, timeout=5, check=False
    )
    assert fixture_snapshot(virtual) == before, "preflight changed configuration or policy"
    assert fixture_snapshot(checkout) == before_checkout, (
        "preflight created bytecode or changed source"
    )
    return result, effects


def fixture_snapshot(root: Path) -> dict[str, tuple[int, bytes | str | None]]:
    result = {}
    for path in root.rglob("*"):
        payload = (
            os.readlink(path)
            if path.is_symlink()
            else path.read_bytes()
            if path.is_file()
            else None
        )
        result[str(path.relative_to(root))] = (path.lstat().st_mode, payload)
    return result
