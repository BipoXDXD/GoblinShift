"""Exercise the shared sender's classification and GoblinShift's digest-bound scan.

The sender still supports both indexes and single images. The GoblinShift scan requires the
approved multiarch index and only pulls its AMD64 manifest. Registry and transport are stubs.
"""

import hashlib
import json
import os
import shutil
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
RUN_HEADER = "        run: |"
RUN_BODY_INDENT = " " * 10
SCRIPT_TIMEOUT_S = 30

INDEX_DIGEST = "sha256:" + "a" * 64
SINGLE_DIGEST = "sha256:" + "b" * 64
OTHER_PLATFORM_DIGEST = "sha256:" + "c" * 64
CANARY = "CANARY-7f3a91"

ROLE_COUNT = 3
FIRST_ROLE = "app"


def classify_error(reason: str) -> str:
    return rf"^Cannot classify registry manifest for role {FIRST_ROLE} \({reason}, jq exit \d+\)$"


def index_error(reason: str) -> str:
    prefix = "Cannot resolve the linux/amd64 digest from the index"
    return rf"^{prefix} for role {FIRST_ROLE} \({reason}\)$"


DIGEST_ERROR = rf"^Refusing digest for role {FIRST_ROLE} \(invalid-digest\)$"


TOOLS_BESIDES_JQ = ("bash", "cat", "dirname", "find", "mkdir", "rm", "tar")

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
    if [[ "$2" == *:sha-* && -f "$STUB_DIR/moved-tag" ]]; then
      cat "$STUB_DIR/moved-tag"
      exit 0
    fi
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
# GNU `mktemp -d` honours TMPDIR, BSD (macOS) ignores it and picks a directory outside the
# sandbox, where the crane stub cannot delete the manifest. This shim gives BSD the GNU behaviour
# so the "absent file" case means the same on every OS; it forwards every other call untouched.
MKTEMP_SHIM = """#!/bin/bash
if [ "$*" = "-d" ]; then
  exec {real_mktemp} -d "$TMPDIR/tmp.XXXXXXXXXX"
fi
exec {real_mktemp} "$@"
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


def extract_run_step(header: str) -> str:
    lines = WORKFLOW.read_text().splitlines()
    step = lines.index(f"      - name: {header}")
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
    step.write_text(extract_run_step("Scan exact published images"))
    # `defaults.run.shell: bash` makes GitHub run each step with exactly these options.
    return ["bash", "--noprofile", "--norc", "-eo", "pipefail", str(step)]


Target = Callable[[Path], list[str]]
TARGETS: tuple[Target, ...] = (send_script_command,)


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


def install_mktemp_shim(bin_dir: Path) -> None:
    real_mktemp = shutil.which("mktemp")
    if real_mktemp is None:
        raise FileNotFoundError("mktemp")
    install_stub(bin_dir, "mktemp", MKTEMP_SHIM.format(real_mktemp=real_mktemp))


def search_path_with_jq(bin_dir: Path) -> str:
    return f"{bin_dir}{os.pathsep}{os.environ['PATH']}"


def search_path_without_jq(bin_dir: Path) -> str:
    """Only the other tools, so the jq call gets a real `command not found` (status 127)."""
    for tool in TOOLS_BESIDES_JQ:
        located = shutil.which(tool)
        if located is None:
            raise FileNotFoundError(tool)
        (bin_dir / tool).symlink_to(located)
    return str(bin_dir)


SearchPath = Callable[[Path], str]


def run_target(
    target: Target,
    manifests: list[str | None],
    search_path: SearchPath = search_path_with_jq,
    single_digest: str = SINGLE_DIGEST,
    approved_index: str | None = None,
    moved_tag: str | None = None,
) -> Outcome:
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
        if target is send_script_command:
            install_stub(bin_dir, "python3", PYTHON_STUB)
        if moved_tag is not None:
            (stub_dir / "moved-tag").write_text(moved_tag)
        install_mktemp_shim(bin_dir)
        write_manifests(stub_dir / "manifests", manifests)
        (stub_dir / "manifest-calls").write_text("0")
        (stub_dir / "requested.tsv").write_text(REQUESTED_ROLES)
        (stub_dir / "calls.log").touch()
        environment = {
            "PATH": search_path(bin_dir),
            "STUB_DIR": str(stub_dir),
            "STUB_SINGLE_DIGEST": single_digest,
            "TMPDIR": str(work_dir),
            "RUNNER_TEMP": str(work_dir),
            "RELEASE_COMMIT": "d" * 40,
            "RELEASE_PROJECT": "goblinshift",
            "DEPLOY_TARGET": "cfrelease-goblinshift@host.example",
            "DEPLOY_KEY": str(stub_dir / "key"),
            "DEPLOY_KNOWN_HOSTS": str(stub_dir / "known_hosts"),
            "TRIVY_IMAGE": "trivy-stub",
        }
        if approved_index is not None:
            environment.update(
                dict.fromkeys(("APP_INDEX", "WEB_INDEX", "EXECUTOR_INDEX"), approved_index)
            )
        completed = subprocess.run(  # noqa: S603 - argv fixo do teste, sem shell
            target(stub_dir),
            cwd=RELEASE_DIR.parents[2],
            env=environment,
            capture_output=True,
            text=True,
            timeout=SCRIPT_TIMEOUT_S,
            check=False,
        )
        calls = tuple((stub_dir / "calls.log").read_text().splitlines())
    return Outcome(completed.returncode, completed.stdout, completed.stderr, calls)


class ImmutableScanTests(unittest.TestCase):
    def test_moved_sha_tag_cannot_choose_the_scanned_image(self) -> None:
        manifest = json.dumps({"schemaVersion": 2, **json.loads(INDEX_MANIFEST)})
        approved_index = "sha256:" + hashlib.sha256(manifest.encode()).hexdigest()
        moved = manifest.replace(INDEX_DIGEST, SINGLE_DIGEST)
        outcome = run_target(
            scan_step_command,
            [manifest] * ROLE_COUNT,
            approved_index=approved_index,
            moved_tag=moved,
        )
        self.assertEqual(outcome.returncode, 0, outcome.stderr)
        self.assertEqual(len(outcome.called("crane manifest")), ROLE_COUNT)
        self.assertTrue(
            all(call.endswith(f"@{approved_index}") for call in outcome.called("crane manifest"))
        )
        self.assertEqual(outcome.pulls_of(INDEX_DIGEST), ROLE_COUNT)
        self.assertEqual(outcome.pulls_of(SINGLE_DIGEST), 0)

    def test_scan_rejects_unapproved_bytes_or_missing_runtime_platform(self) -> None:
        index = {"schemaVersion": 2, **json.loads(INDEX_MANIFEST)}
        manifest = json.dumps(index)
        digest = "sha256:" + hashlib.sha256(manifest.encode()).hexdigest()
        incomplete = {
            "schemaVersion": 2,
            "manifests": [index["manifests"][0]],
        }
        incomparable = {
            "schemaVersion": 2,
            "manifests": [*index["manifests"], index["manifests"][0]],
        }
        invalid_child = {
            "schemaVersion": 2,
            "manifests": [
                {**index["manifests"][0], "digest": "sha256:invalid"},
                index["manifests"][1],
            ],
        }
        for body, approved in [
            (manifest + " ", digest),
            *(
                (raw, "sha256:" + hashlib.sha256(raw.encode()).hexdigest())
                for raw in map(json.dumps, (incomplete, incomparable, invalid_child))
            ),
        ]:
            with self.subTest(body=body, approved=approved):
                outcome = run_target(scan_step_command, [body], approved_index=approved)
                self.assertNotEqual(outcome.returncode, 0)
                self.assertEqual(outcome.called("docker"), ())
                self.assertEqual(outcome.called("crane digest"), ())
                self.assertEqual(outcome.called("ssh"), ())


class ManifestClassificationTests(unittest.TestCase):
    def assert_release_stopped(self, outcome: Outcome, message: str) -> None:
        self.assertEqual(outcome.returncode, 1, outcome.stderr)
        self.assertRegex(outcome.stderr.strip(), message)
        self.assertNotIn(CANARY, outcome.stdout + outcome.stderr)
        self.assertEqual(outcome.called("crane digest"), ())
        self.assertEqual(outcome.called("crane pull"), ())
        self.assertEqual(outcome.called("docker"), ())
        self.assertEqual(outcome.called("ssh"), ())

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
        unreadable: dict[str, tuple[str | None, str]] = {
            "invalid_json": ('{"token": "' + CANARY, "unreadable"),
            "absent_file": (None, "unreadable"),
            "empty_file": ("", "not-one-object"),
            "json_array": (f'["{CANARY}"]', "not-one-object"),
            "null_literal": ("null", "not-one-object"),
            "only_whitespace": (" \n", "not-one-object"),
            "index_then_object": (f"{INDEX_MANIFEST}\n{SINGLE_MANIFEST}", "not-one-object"),
            "object_then_index": (f"{SINGLE_MANIFEST}\n{INDEX_MANIFEST}", "not-one-object"),
            "index_then_null": (f"{INDEX_MANIFEST}\nnull", "not-one-object"),
            "null_then_index": (f"null\n{INDEX_MANIFEST}", "not-one-object"),
            "two_objects": (f"{SINGLE_MANIFEST}\n{SINGLE_MANIFEST}", "not-one-object"),
        }
        for target in TARGETS:
            for case, (manifest, reason) in unreadable.items():
                with self.subTest(target=target.__name__, case=case):
                    outcome = run_target(target, [manifest] * ROLE_COUNT)

                    self.assert_release_stopped(outcome, classify_error(reason))

    def test_missing_jq_stops_the_release(self) -> None:
        for target in TARGETS:
            with self.subTest(target=target.__name__):
                outcome = run_target(target, [INDEX_MANIFEST] * ROLE_COUNT, search_path_without_jq)

                self.assert_release_stopped(outcome, classify_error("jq-missing"))

    def test_index_without_one_amd64_digest_stops_the_release(self) -> None:
        linux = {"os": "linux", "architecture": "amd64"}
        amd64 = {"digest": INDEX_DIGEST, "platform": linux}
        arm64 = {"digest": OTHER_PLATFORM_DIGEST, "platform": {**linux, "architecture": "arm64"}}
        unusable: dict[str, tuple[object, str]] = {
            "manifests_string": (CANARY, "manifests-not-list"),
            "manifests_null": (None, "manifests-not-list"),
            "manifests_object": ({"only": amd64}, "manifests-not-list"),
            "no_amd64": ([arm64], "no-amd64"),
            "two_amd64": ([amd64, amd64], "multiple-amd64"),
            "entry_not_object": ([CANARY], "invalid-entry"),
        }
        for target in TARGETS:
            for case, (manifests, reason) in unusable.items():
                with self.subTest(target=target.__name__, case=case):
                    manifest = json.dumps({"manifests": manifests})

                    outcome = run_target(target, [manifest] * ROLE_COUNT)

                    self.assert_release_stopped(outcome, index_error(reason))

    def test_digest_fora_do_formato_sha256_para_antes_do_pull(self) -> None:
        malformed = {
            "numero": "42",
            "canario": CANARY,
            "vazio": "",
            "hex_curto": "sha256:" + "a" * 63,
            "hex_longo": "sha256:" + "a" * 65,
            "maiusculas": "sha256:" + "A" * 64,
            "outro_algoritmo": "sha512:" + "a" * 64,
            "sem_prefixo": "a" * 64,
            "nova_linha_no_fim": f"{INDEX_DIGEST}\nsha256:{CANARY}",
        }
        for target in TARGETS:
            for case, digest in malformed.items():
                with self.subTest(target=target.__name__, source="index", case=case):
                    platform = {"os": "linux", "architecture": "amd64"}
                    manifest = json.dumps({"manifests": [{"digest": digest, "platform": platform}]})

                    outcome = run_target(target, [manifest] * ROLE_COUNT)

                    self.assert_release_stopped(outcome, DIGEST_ERROR)
                with self.subTest(target=target.__name__, source="registry", case=case):
                    outcome = run_target(
                        target, [SINGLE_MANIFEST] * ROLE_COUNT, single_digest=digest
                    )

                    self.assert_digest_refused_after_lookup(outcome)

    def assert_digest_refused_after_lookup(self, outcome: Outcome) -> None:
        self.assertEqual(outcome.returncode, 1, outcome.stderr)
        self.assertRegex(outcome.stderr.strip(), DIGEST_ERROR)
        self.assertNotIn(CANARY, outcome.stdout + outcome.stderr)
        self.assertEqual(len(outcome.called("crane digest")), 1)
        self.assertEqual(outcome.called("crane pull"), ())
        self.assertEqual(outcome.called("docker"), ())
        self.assertEqual(outcome.called("ssh"), ())


if __name__ == "__main__":
    unittest.main()
