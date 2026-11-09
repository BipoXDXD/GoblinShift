"""send.sh and the workflow scan step tell an OCI index from a single image by jq's exit status.

Only status 1 means "not an index". Any other failure must stop the release instead of falling
through to `crane digest`. Registry and transport commands are stubs; jq and bash are real.
"""

import json
import os
import stat
import subprocess
import tempfile
import unittest
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

RELEASE_DIR = Path(__file__).resolve().parent
SEND_SCRIPT = RELEASE_DIR / "send.sh"
WORKFLOW = RELEASE_DIR.parents[2] / ".github" / "workflows" / "coolify.yml"
SCAN_STEP_HEADER = "      - name: Scan exact published images"
RUN_HEADER = "        run: |"
RUN_BODY_INDENT = " " * 10
SCRIPT_TIMEOUT_S = 30

INDEX_DIGEST = "sha256:" + "a" * 64
SINGLE_DIGEST = "sha256:" + "b" * 64
OTHER_PLATFORM_DIGEST = "sha256:" + "c" * 64
CANARY = "CANARY-7f3a91"

ROLE_COUNT = 3
FIRST_ROLE = "app"
ERROR_MESSAGE = rf"^Cannot classify registry manifest for role {FIRST_ROLE} \(jq exit \d+\)$"

INDEX_MANIFEST = json.dumps(
    {
        "manifests": [
            {"digest": INDEX_DIGEST, "platform": {"os": "linux", "architecture": "amd64"}},
            {"digest": OTHER_PLATFORM_DIGEST, "platform": {"os": "linux", "architecture": "arm64"}},
        ]
    }
)
SINGLE_MANIFEST = json.dumps({"schemaVersion": 2, "config": {}})

REQUESTED_ROLES = "".join(
    f"{role}\tghcr.io/bipoxdxd/{image}@{SINGLE_DIGEST}\n"
    for role, image in (
        (FIRST_ROLE, "goblinshift"),
        ("web", "goblinshift-web"),
        ("executor", "goblinshift-executor"),
    )
)

CRANE_STUB = """#!/bin/bash
set -eu
printf 'crane %s\\n' "$*" >> "$STUB_DIR/calls.log"
case "$1" in
  manifest)
    call=$(cat "$STUB_DIR/manifest-calls")
    printf '%s' "$((call + 1))" > "$STUB_DIR/manifest-calls"
    if [ -e "$STUB_DIR/manifests/$call.absent" ]; then
      find "$RUNNER_TEMP" -maxdepth 2 \\( -name index.json -o -name scan-index.json \\) -delete
      exit 0
    fi
    cat "$STUB_DIR/manifests/$call"
    ;;
  digest)
    printf '%s\\n' "$STUB_SINGLE_DIGEST"
    ;;
  pull)
    target=${!#}
    mkdir -p "$target"
    printf '{"manifests":[]}' > "$target/index.json"
    ;;
  *)
    exit 64
    ;;
esac
"""
DOCKER_STUB = """#!/bin/bash
printf 'docker %s\\n' "$*" >> "$STUB_DIR/calls.log"
"""
SSH_STUB = """#!/bin/bash
printf 'ssh\\n' >> "$STUB_DIR/calls.log"
cat > /dev/null
"""
PYTHON_STUB = """#!/bin/bash
if [ "$2" = requested ]; then
  cat "$STUB_DIR/requested.tsv"
fi
"""


@dataclass(frozen=True)
class Outcome:
    returncode: int
    stdout: str
    stderr: str
    calls: tuple[str, ...]

    def called(self, prefix: str) -> tuple[str, ...]:
        return tuple(call for call in self.calls if call.startswith(prefix))

    def pulls_of(self, digest: str) -> int:
        return sum(" pull " in call and f"@{digest}" in call for call in self.calls)


def extract_scan_step() -> str:
    lines = WORKFLOW.read_text().splitlines()
    step = lines.index(SCAN_STEP_HEADER)
    start = lines.index(RUN_HEADER, step) + 1
    body = []
    for line in lines[start:]:
        if line and not line.startswith(RUN_BODY_INDENT):
            break
        body.append(line.removeprefix(RUN_BODY_INDENT))
    return "\n".join(body) + "\n"


def send_script_command(stub_dir: Path) -> list[str]:
    return ["bash", str(SEND_SCRIPT)]


def scan_step_command(stub_dir: Path) -> list[str]:
    step = stub_dir / "scan-step.sh"
    step.write_text(extract_scan_step())
    # `defaults.run.shell: bash` makes GitHub run each step with exactly these options.
    return ["bash", "--noprofile", "--norc", "-eo", "pipefail", str(step)]


Target = Callable[[Path], list[str]]
TARGETS: tuple[Target, ...] = (send_script_command, scan_step_command)


def install_stub(bin_dir: Path, name: str, content: str) -> None:
    stub = bin_dir / name
    stub.write_text(content)
    stub.chmod(stat.S_IRWXU)


def write_manifests(manifests_dir: Path, manifests: list[str | None]) -> None:
    manifests_dir.mkdir()
    for call, manifest in enumerate(manifests):
        if manifest is None:
            (manifests_dir / f"{call}.absent").touch()
        else:
            (manifests_dir / str(call)).write_text(manifest)


def run_target(target: Target, manifests: list[str | None]) -> Outcome:
    with tempfile.TemporaryDirectory() as raw_root:
        root = Path(raw_root)
        stub_dir = root / "stub"
        bin_dir = stub_dir / "bin"
        work_dir = root / "work"
        bin_dir.mkdir(parents=True)
        work_dir.mkdir()
        install_stub(bin_dir, "crane", CRANE_STUB)
        install_stub(bin_dir, "docker", DOCKER_STUB)
        install_stub(bin_dir, "ssh", SSH_STUB)
        install_stub(bin_dir, "python3", PYTHON_STUB)
        write_manifests(stub_dir / "manifests", manifests)
        (stub_dir / "manifest-calls").write_text("0")
        (stub_dir / "requested.tsv").write_text(REQUESTED_ROLES)
        (stub_dir / "calls.log").touch()
        environment = {
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "STUB_DIR": str(stub_dir),
            "STUB_SINGLE_DIGEST": SINGLE_DIGEST,
            "TMPDIR": str(work_dir),
            "RUNNER_TEMP": str(work_dir),
            "RELEASE_COMMIT": "d" * 40,
            "RELEASE_PROJECT": "goblinshift",
            "DEPLOY_TARGET": "cfrelease-goblinshift@host.example",
            "DEPLOY_KEY": str(stub_dir / "key"),
            "DEPLOY_KNOWN_HOSTS": str(stub_dir / "known_hosts"),
            "TRIVY_IMAGE": "trivy-stub",
        }
        completed = subprocess.run(
            target(stub_dir),
            env=environment,
            capture_output=True,
            text=True,
            timeout=SCRIPT_TIMEOUT_S,
            check=False,
        )
        calls = tuple((stub_dir / "calls.log").read_text().splitlines())
    return Outcome(completed.returncode, completed.stdout, completed.stderr, calls)


class ManifestClassificationTests(unittest.TestCase):
    def test_index_resolves_platform_digest_without_asking_the_registry(self) -> None:
        for target in TARGETS:
            with self.subTest(target=target.__name__):
                outcome = run_target(target, [INDEX_MANIFEST] * ROLE_COUNT)

                self.assertEqual(outcome.returncode, 0, outcome.stderr)
                self.assertEqual(outcome.called("crane digest"), ())
                self.assertEqual(outcome.pulls_of(INDEX_DIGEST), ROLE_COUNT)

    def test_single_image_uses_the_registry_digest(self) -> None:
        for target in TARGETS:
            with self.subTest(target=target.__name__):
                outcome = run_target(target, [SINGLE_MANIFEST] * ROLE_COUNT)

                self.assertEqual(outcome.returncode, 0, outcome.stderr)
                self.assertEqual(len(outcome.called("crane digest")), ROLE_COUNT)
                self.assertEqual(outcome.pulls_of(SINGLE_DIGEST), ROLE_COUNT)

    def test_each_role_is_classified_on_its_own(self) -> None:
        for target in TARGETS:
            with self.subTest(target=target.__name__):
                outcome = run_target(target, [SINGLE_MANIFEST, INDEX_MANIFEST, SINGLE_MANIFEST])

                self.assertEqual(outcome.returncode, 0, outcome.stderr)
                self.assertEqual(len(outcome.called("crane digest")), 2)
                self.assertEqual(outcome.pulls_of(INDEX_DIGEST), 1)

    def test_unreadable_manifest_stops_the_release(self) -> None:
        unreadable: dict[str, str | None] = {
            "invalid_json": '{"token": "' + CANARY,
            "absent_file": None,
            "empty_file": "",
            "json_array": f'["{CANARY}"]',
        }
        for target in TARGETS:
            for case, manifest in unreadable.items():
                with self.subTest(target=target.__name__, case=case):
                    outcome = run_target(target, [manifest] * ROLE_COUNT)

                    self.assertEqual(outcome.returncode, 1, outcome.stderr)
                    self.assertRegex(outcome.stderr.strip(), ERROR_MESSAGE)
                    self.assertNotIn(CANARY, outcome.stdout + outcome.stderr)
                    self.assertEqual(outcome.called("crane digest"), ())
                    self.assertEqual(outcome.called("crane pull"), ())
                    self.assertEqual(outcome.called("docker"), ())
                    self.assertEqual(outcome.called("ssh"), ())


if __name__ == "__main__":
    unittest.main()
