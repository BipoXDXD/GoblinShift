"""Run the production publication steps against a disposable registry stub."""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from ci_oci import INDEX, ROLES
from test_manifest_classification import RELEASE_DIR, extract_run_step, install_stub

SHA = "a" * 40
TAG = f"sha-{SHA[:12]}"
REPOSITORIES = {
    "app": "ghcr.io/bipoxdxd/goblinshift",
    "executor": "ghcr.io/bipoxdxd/goblinshift-executor",
    "web": "ghcr.io/bipoxdxd/goblinshift-web",
}
PUBLISH = "Publish exactly the three already verified sha tags"
PROMOTE = "Promote the three indexes by digest and verify the tags"

CRANE = r"""
import json, os, sys
from pathlib import Path

registry = Path(os.environ['FAKE_REGISTRY'])
tags = json.loads(registry.read_text())
op, *args = sys.argv[1:]
with (registry.parent / 'calls').open('a') as output:
    output.write(json.dumps([op, *args]) + '\n')
if op == 'auth':
    sys.stdin.read()
elif op == 'digest':
    tag = args[0]
    if tag not in tags:
        sys.exit('MANIFEST_UNKNOWN')
    print(tags[tag])
elif op == 'push':
    layout, tag = args
    descriptor, = json.loads((Path(layout) / 'index.json').read_text())['manifests']
    tags[tag] = descriptor['digest']
    registry.write_text(json.dumps(tags))
    if tag == os.environ.get('INTERRUPT_AFTER_WRITE'):
        sys.exit('Connection lost after registry committed the tag')
elif op == 'tag':
    ref, tag = args
    repository, digest = ref.split('@')
    if not any(key.startswith(repository + ':') and val == digest for key, val in tags.items()):
        sys.exit('MANIFEST_UNKNOWN')
    tags[repository + ':' + tag] = digest
    registry.write_text(json.dumps(tags))
else:
    sys.exit(f'Unexpected registry operation: {op}')
"""


class PartialPublicationTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.registry = self.root / "registry.json"
        self.registry.write_text("{}")
        self.bin_dir = self.root / "bin"
        self.bin_dir.mkdir()
        install_stub(self.bin_dir, "crane", f"#!{sys.executable}\n{CRANE}")

    def attempt(self, number: int, changed: str = "") -> tuple[Path, dict[str, str]]:
        runner = self.root / f"runner-{number}"
        images = {}
        for i, role in enumerate(ROLES, 1):
            layout = runner / "ci-oci" / f"gs-oci-{role}-{number}"
            blobs = layout / "blobs" / "sha256"
            blobs.mkdir(parents=True)
            index = {
                "schemaVersion": 2,
                "annotations": {"version": "rebuilt" if changed == role else "approved"},
                "manifests": [
                    {
                        "platform": {"os": "linux", "architecture": arch},
                        "digest": "sha256:" + str(i) * 64,
                    }
                    for arch in ("amd64", "arm64")
                ],
            }
            raw = json.dumps(index).encode()
            digest = hashlib.sha256(raw).hexdigest()
            (blobs / digest).write_bytes(raw)
            descriptor = {"mediaType": INDEX, "size": len(raw), "digest": f"sha256:{digest}"}
            (layout / "index.json").write_text(json.dumps({"manifests": [descriptor]}))
            images[role] = {"artifact": str(10 * number + i), "digest": descriptor["digest"]}
        receipt = runner / "oci-receipt" / "oci-receipt.json"
        receipt.parent.mkdir()
        receipt.write_text(json.dumps({"images": images}))
        return runner, {f"{REPOSITORIES[role]}:{TAG}": images[role]["digest"] for role in ROLES}

    def step(
        self, name: str, runner: Path, interrupted: str = ""
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603 - bash executes the checked-in workflow under test.
            ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", extract_run_step(name)],  # noqa: S607
            cwd=RELEASE_DIR.parents[2],
            env={
                "PATH": f"{self.bin_dir}{os.pathsep}{os.environ['PATH']}",
                "GITHUB_ACTIONS": "true",
                "GITHUB_REPOSITORY": "BipoXDXD/GoblinShift",
                "GITHUB_REF": "refs/heads/main",
                "GITHUB_EVENT_NAME": "workflow_dispatch",
                "GITHUB_ACTOR": "fixture",
                "GH_TOKEN": "fixture",
                "RELEASE_COMMIT": SHA,
                "RUNNER_TEMP": str(runner),
                "FAKE_REGISTRY": str(self.registry),
                "INTERRUPT_AFTER_WRITE": interrupted,
            },
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

    def pushes(self) -> list[str]:
        calls = (self.root / "calls").read_text().splitlines()
        return [args[-1] for call in calls if (args := json.loads(call))[0] == "push"]

    def test_partial_write_recovers_with_a_new_ci_receipt_and_replays_original(self) -> None:
        first, approved = self.attempt(1)
        second, rebuilt = self.attempt(2)
        self.assertEqual(rebuilt, approved)
        executor_tag = f"{REPOSITORIES['executor']}:{TAG}"
        result = self.step(PUBLISH, first, interrupted=executor_tag)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Connection lost after registry committed", result.stderr)
        self.assertEqual(len(json.loads(self.registry.read_text())), 2)
        self.assertEqual(len(self.pushes()), 2)

        result = self.step(PUBLISH, second)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(self.registry.read_text()), approved)
        self.assertEqual(self.pushes()[2:], [f"{REPOSITORIES['web']}:{TAG}"])
        result = self.step(PROMOTE, second)
        self.assertEqual(result.returncode, 0, result.stderr)
        restored = {
            **approved,
            **{key.removesuffix(TAG) + "main": val for key, val in approved.items()},
        }
        self.assertEqual(json.loads(self.registry.read_text()), restored)

        result = self.step(PUBLISH, first)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.pushes()), 3)
        self.assertEqual(json.loads(self.registry.read_text()), restored)

    def test_rebuilt_divergence_stops_before_writing_missing_siblings(self) -> None:
        first, approved = self.attempt(1)
        rebuilt, _ = self.attempt(2, changed="executor")
        executor_tag = f"{REPOSITORIES['executor']}:{TAG}"
        partial = {executor_tag: approved[executor_tag]}
        self.registry.write_text(json.dumps(partial))

        result = self.step(PUBLISH, rebuilt)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Existing release tag differs", result.stderr)
        self.assertEqual(self.pushes(), [])
        self.assertEqual(json.loads(self.registry.read_text()), partial)

        result = self.step(PUBLISH, first)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.pushes(), [f"{REPOSITORIES[role]}:{TAG}" for role in ("app", "web")])
        self.assertEqual(json.loads(self.registry.read_text()), approved)
