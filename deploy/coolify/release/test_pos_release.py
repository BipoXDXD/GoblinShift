"""Post-release decision (pos_release.py) and its thin collector (pos_release.sh).

The fixture holds the facts read from the real release 691dcbaf on 2026-11-11: commits, digests,
hashes and epochs are public. The Coolify UUIDs are placeholders, and the allowlist entries after
the first are synthetic (only its top was recorded). The collector tests stub ssh, curl, crane,
gh and git; bash, jq and python3 are real.
"""

import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import pos_release as p

RELEASE_DIR = Path(__file__).resolve().parent
FIXTURE = RELEASE_DIR / "fixtures" / "pos-release-691dcbaf.tsv"
COLLECTOR = RELEASE_DIR / "pos_release.sh"
COMMIT = "691dcbaf60feb888215b7c3266086e243b04f9e1"
OTHER_COMMIT = "d96d64b97f7a" + "0" * 28
APP_INDEX_DIGEST = "sha256:110ffe984d13c58ca6a378141c2bafc729fd181dd86d3d7ce0dff8bf420c8f2b"
APP_AMD64_DIGEST = "sha256:831beab7ce340857c612cf57f8a08977379cc0649d8d9298823a857c4e284308"
EXECUTOR = (
    "ghcr.io/bipoxdxd/goblinshift-executor@"
    "sha256:83d663ee19321b55ac22512cbf14778b03c5e97627608ca2fac330894c654a9c"
)
OUTSIDE_KEYS = ("RECV_REPO", "MANIFEST_", "TAG_DIGEST_", "RUN_", "MAIN_HEAD")
UNUSED_FOR_INDEX = ("TAG_DIGEST_APP", "TAG_DIGEST_WEB", "TAG_DIGEST_EXECUTOR")
SCRIPT_TIMEOUT_S = 30


def fixture_lines() -> list[tuple[str, str]]:
    lines = []
    for line in FIXTURE.read_text().splitlines():
        key, _, value = line.partition("\t")
        lines.append((key, value))
    return lines


def as_text(lines: list[tuple[str, str]]) -> str:
    return "".join(f"{key}\t{value}\n" for key, value in lines)


def with_value(key: str, value: str) -> str:
    return as_text([(k, value if k == key else v) for k, v in fixture_lines()])


def without(key: str) -> str:
    return as_text([(k, v) for k, v in fixture_lines() if k != key])


def decide(text: str, commit: str = COMMIT) -> p.Verdict:
    return p.evaluate(commit, p.parse_facts(text))


def outcome(verdict: p.Verdict, item: str) -> p.Outcome:
    [check] = [check for check in verdict.checks if check.item == item]
    return check.outcome


class CoherentReleaseTests(unittest.TestCase):
    def test_real_release_691dcbaf_is_coherent(self) -> None:
        verdict = decide(as_text(fixture_lines()))
        self.assertEqual(verdict.status, p.Status.COERENTE)
        self.assertEqual({check.outcome for check in verdict.checks}, {p.Outcome.OK})
        self.assertEqual(len(verdict.checks), 31)

    def test_coherent_exit_code_is_zero(self) -> None:
        with redirect_stdout(io.StringIO()) as out:
            code = p.main(["pos_release.py", COMMIT], as_text(fixture_lines()))
        self.assertEqual(code, 0)
        self.assertEqual(out.getvalue().splitlines()[-1], "COERENTE")


class InProgressTests(unittest.TestCase):
    def test_pending_json_alone_means_in_progress(self) -> None:
        verdict = decide("PENDING\t/var/lib/coolify-release/goblinshift/pending.json\n")
        self.assertEqual(verdict.status, p.Status.EM_ANDAMENTO)
        self.assertEqual(verdict.status.value, 3)

    def test_pending_json_wins_over_otherwise_coherent_facts(self) -> None:
        text = as_text(fixture_lines()) + "PENDING\t/var/lib/coolify-release/pending.json\n"
        self.assertEqual(decide(text).status, p.Status.EM_ANDAMENTO)


class IncoherentReleaseTests(unittest.TestCase):
    def assert_incoherent_at(self, text: str, item: str) -> None:
        verdict = decide(text)
        self.assertEqual(verdict.status, p.Status.INCOERENTE)
        self.assertEqual(verdict.status.value, 1)
        self.assertEqual(outcome(verdict, item), p.Outcome.FALHA)

    def test_current_json_pointing_to_another_commit(self) -> None:
        self.assert_incoherent_at(with_value("COMMIT", OTHER_COMMIT), "7")

    def test_receipt_digest_is_the_index_not_the_published_amd64(self) -> None:
        images = json.loads(dict(fixture_lines())["IMAGES"])
        images["app"] = images["app"].replace(APP_AMD64_DIGEST, APP_INDEX_DIGEST)
        self.assert_incoherent_at(with_value("IMAGES", json.dumps(images)), "11 app")

    def test_index_with_two_amd64_entries_is_ambiguous(self) -> None:
        manifest = json.loads(dict(fixture_lines())["MANIFEST_APP"])
        manifest["manifests"].append(dict(manifest["manifests"][0], digest=APP_INDEX_DIGEST))
        self.assert_incoherent_at(with_value("MANIFEST_APP", json.dumps(manifest)), "11 app")

    def test_lock_still_held(self) -> None:
        self.assert_incoherent_at(with_value("LOCK_PROJETO", "preso"), "5")

    def test_unhealthy_container(self) -> None:
        lines = [
            (k, v.replace("|healthy|", "|unhealthy|") if v.startswith("goblinshift-api-1|") else v)
            for k, v in fixture_lines()
        ]
        self.assert_incoherent_at(as_text(lines), "22 goblinshift-api-1")

    def test_readyz_other_than_200(self) -> None:
        self.assert_incoherent_at(with_value("READYZ", "503"), "23")

    def test_allowlist_with_duplicate(self) -> None:
        self.assert_incoherent_at(with_value("ALLOWED", json.dumps([EXECUTOR, EXECUTOR])), "18")

    def test_history_without_entry_for_the_commit(self) -> None:
        text = with_value("HIST_TOPO", f"{OTHER_COMMIT}-1794355000.json").replace(
            "HIST_N_COMMIT\t1", "HIST_N_COMMIT\t0"
        )
        self.assert_incoherent_at(text, "14")

    def test_failure_wins_over_unknown(self) -> None:
        text = with_value("READYZ", "503").replace("LOCK_HOST\tlivre\n", "")
        self.assertEqual(decide(text).status, p.Status.INCOERENTE)


class UnknownTests(unittest.TestCase):
    def test_any_missing_fact_is_unknown_never_coherent(self) -> None:
        keys = {key for key, _ in fixture_lines()} - set(UNUSED_FOR_INDEX)
        for key in sorted(keys):
            with self.subTest(key=key):
                verdict = decide(without(key))
                self.assertEqual(verdict.status, p.Status.DESCONHECIDO)
                self.assertEqual(verdict.status.value, 2)

    def test_single_image_manifest_needs_the_tag_digest(self) -> None:
        text = with_value("MANIFEST_APP", json.dumps({"schemaVersion": 2, "config": {}}))
        self.assertEqual(decide(text.replace(f"TAG_DIGEST_APP\t{APP_INDEX_DIGEST}\n", "")).status,
                         p.Status.DESCONHECIDO)  # fmt: skip

    def test_repeated_single_value_fact_is_unknown(self) -> None:
        text = as_text(fixture_lines()) + f"COMMIT\t{COMMIT}\n"
        self.assertEqual(decide(text).status, p.Status.DESCONHECIDO)

    def test_malformed_collection_line_is_unknown(self) -> None:
        with redirect_stdout(io.StringIO()):
            code = p.main(["pos_release.py", COMMIT], as_text(fixture_lines()) + "lixo sem tab\n")
        self.assertEqual(code, 2)

    def test_abbreviated_commit_is_unknown(self) -> None:
        self.assertEqual(
            decide(as_text(fixture_lines()), COMMIT[:12]).status, p.Status.DESCONHECIDO
        )


class CollectorTests(unittest.TestCase):
    """pos_release.sh only collects: stubs stand in for every network tool."""

    def setUp(self) -> None:
        self.stubs = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.stubs)
        facts = fixture_lines()
        remote = [(k, v) for k, v in facts if not k.startswith(OUTSIDE_KEYS)]
        (self.stubs / "remote.tsv").write_text(as_text(remote))
        values = dict(facts)
        for role in ("APP", "WEB", "EXECUTOR"):
            (self.stubs / f"manifest-{role}").write_text(values[f"MANIFEST_{role}"])
            (self.stubs / f"digest-{role}").write_text(values[f"TAG_DIGEST_{role}"])
        self.stub("ssh", 'cat "$STUB_DIR/remote.tsv"')
        self.stub("curl", f"""printf '{{"sha256":"%s"}}' '{values["RECV_REPO"]}'""")
        self.stub(
            "crane",
            'case "$2" in *executor*) r=EXECUTOR;; *web*) r=WEB;; *) r=APP;; esac\n'
            'cat "$STUB_DIR/$1-$r"',
        )
        self.stub("gh", f"printf '%s\\t%s\\t%s\\n' {values['RUN_CRIADO']} completed success")
        self.stub("git", f"printf '%s\\trefs/heads/main\\n' {COMMIT}")

    def stub(self, name: str, body: str) -> None:
        path = self.stubs / name
        log_call = f'printf "%s\\n" "{name} $*" >> "$STUB_DIR/calls.log"'
        path.write_text(f"#!/bin/bash\n{log_call}\n{body}\n")
        path.chmod(0o755)

    def run_collector(self, *args: str, target: str | None = "destino-de-teste") -> int:
        env = {"PATH": f"{self.stubs}:{os.environ['PATH']}", "STUB_DIR": str(self.stubs)}
        if target is not None:
            env["POS_RELEASE_SSH_TARGET"] = target
        result = subprocess.run(  # noqa: S603 - argv fixo do teste, sem shell
            ["bash", str(COLLECTOR), *args],  # noqa: S607 - bash do PATH, como o job release-tools
            env=env,
            capture_output=True,
            text=True,
            timeout=SCRIPT_TIMEOUT_S,
            check=False,
        )
        return result.returncode

    def calls(self) -> str:
        log = self.stubs / "calls.log"
        return log.read_text() if log.exists() else ""

    def test_collected_real_facts_are_coherent(self) -> None:
        self.assertEqual(self.run_collector(COMMIT), 0)
        self.assertIn("ssh -o BatchMode=yes -o ConnectTimeout=15 -- destino-de-teste", self.calls())

    def test_ssh_target_is_required_without_default(self) -> None:
        self.assertEqual(self.run_collector(COMMIT, target=None), 2)
        self.assertEqual(self.calls(), "")

    def test_ssh_target_cannot_be_an_option(self) -> None:
        self.assertEqual(self.run_collector(COMMIT, target="-oProxyCommand=x"), 2)
        self.assertEqual(self.calls(), "")

    def test_pending_stops_before_reading_outside_the_vps(self) -> None:
        self.stub("ssh", "printf 'PENDING\\t/var/lib/coolify-release/pending.json\\n'")
        self.assertEqual(self.run_collector(COMMIT), 3)
        self.assertNotIn("crane", self.calls())

    def test_failed_remote_read_is_unknown(self) -> None:
        self.stub("ssh", "exit 255")
        self.assertEqual(self.run_collector(COMMIT), 2)

    def test_failed_registry_read_is_unknown(self) -> None:
        self.stub("crane", "exit 1")
        self.assertEqual(self.run_collector(COMMIT), 2)


if __name__ == "__main__":
    unittest.main()
