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
OTHER_DIGEST = "sha256:" + "f" * 64
ALLOWLIST = "/var/lib/coolify-release/goblinshift/executor-images.json"
API = "goblinshift-" + "api-1"  # partido: o gitleaks o lia como chave
NGINX = "goblinshift-nginx-1"
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


def with_container(name: str, field: int, value: str) -> str:
    """Changes one `|` field (0 = name) of the CONTAINER line of one container."""
    lines = []
    for key, line in fixture_lines():
        parts = line.split("|")
        if key == "CONTAINER" and parts[0] == name:
            parts[field] = value
        lines.append((key, "|".join(parts)))
    return as_text(lines)


def with_image(role: str, reference: str | None) -> str:
    images = json.loads(dict(fixture_lines())["IMAGES"])
    if reference is None:
        del images[role]
    else:
        images[role] = reference
    return with_value("IMAGES", json.dumps(images))


def with_amd64(role: str, digest: str) -> str:
    key = f"MANIFEST_{role.upper()}"
    manifest = json.loads(dict(fixture_lines())[key])
    manifest["manifests"][0]["digest"] = digest
    return with_value(key, json.dumps(manifest))


def with_values(changes: dict[str, str]) -> str:
    return as_text([(k, changes.get(k, v)) for k, v in fixture_lines()])


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


FALHA = p.Outcome.FALHA
DESCONHECIDO = p.Outcome.DESCONHECIDO
HISTORY = f"{COMMIT}-1794355749.json"

# (case, collected facts, item, expected outcome of the item, other items also not OK).
# One wrong value per case; every item of the checklist has at least one.
ITEM_FAILURES: list[tuple[str, str, str, p.Outcome, frozenset[str]]] = [
    ("pending não confirmado", without("PENDING_LIDO"), "1-2", DESCONHECIDO, frozenset()),
    ("pending de outro projeto",
     with_value("OUTROS_PENDING", "/var/lib/coolify-release/duora/pending.json"),
     "3", FALHA, frozenset()),
    ("incoming órfão", with_value("INCOMING", "/var/lib/coolify-release/goblinshift/incoming-x"),
     "4", FALHA, frozenset()),
    ("host.lock preso", with_value("LOCK_HOST", "preso"), "5", FALHA, frozenset()),
    ("release.lock preso", with_value("LOCK_PROJETO", "preso"), "5", FALHA, frozenset()),
    ("estado de lock estranho", with_value("LOCK_HOST", "talvez"), "5", DESCONHECIDO, frozenset()),
    ("receptor ativo",
     with_value("RECEPTOR_ATIVO", "4242 python3 /opt/coolify-release/receiver.py goblinshift"),
     "6", FALHA, frozenset()),
    ("current de outro commit", with_value("COMMIT", OTHER_COMMIT), "7", FALHA, frozenset()),
    ("current sem commit", with_value("COMMIT", "null"), "7", FALHA, frozenset()),
    ("outro projeto", with_value("PROJETO", "duora"), "8", FALHA, frozenset()),
    ("outro protocolo", with_value("VERSAO", "1"), "8", FALHA, frozenset()),
    ("hash do recibo", with_value("RECV_SHA", "f" * 64), "9", FALHA, frozenset()),
    ("hash instalado", with_value("RECV_INST", "f" * 64), "9", FALHA, frozenset()),
    ("hash do repo", with_value("RECV_REPO", "f" * 64), "9", FALHA, frozenset()),
    ("hashes iguais e inválidos",
     with_values({"RECV_SHA": "null", "RECV_INST": "null", "RECV_REPO": "null"}),
     "9", FALHA, frozenset()),
    ("papel a mais", with_image("extra", "ghcr.io/bipoxdxd/goblinshift@" + OTHER_DIGEST),
     "10", FALHA, frozenset()),
    ("papel por tag", with_image("web", "ghcr.io/bipoxdxd/goblinshift-web:latest"), "10", FALHA,
     frozenset({"11 web", "21 web", "22 " + NGINX})),
    ("app amd64 publicado diferente", with_amd64("app", OTHER_DIGEST), "11 app", FALHA,
     frozenset()),
    ("web amd64 publicado diferente", with_amd64("web", OTHER_DIGEST), "11 web", FALHA,
     frozenset()),
    ("executor amd64 publicado diferente", with_amd64("executor", OTHER_DIGEST), "11 executor",
     FALHA, frozenset()),
    ("deployment_uuid vazio", with_value("DEPLOY_UUID", ""), "12", FALHA, frozenset()),
    ("deployment_uuid null", with_value("DEPLOY_UUID", "null"), "12", FALHA, frozenset()),
    ("finished_at igual ao run", with_value("RUN_CRIADO", "1794355749"), "13", FALHA,
     frozenset()),
    ("history com duas entradas do commit", with_value("HIST_N_COMMIT", "2"), "14", FALHA,
     frozenset()),
    ("history sem entradas do commit", with_value("HIST_N_COMMIT", "0"), "14", FALHA,
     frozenset()),
    ("history difere do current", with_value("HIST_IGUAL", "false"), "15", FALHA, frozenset()),
    ("epoch do history antes do finished_at", with_value("FINISHED", "1794355750"), "16", FALHA,
     frozenset()),
    ("epoch do history 6 s depois", with_value("FINISHED", "1794355743"), "16", FALHA,
     frozenset()),
    ("executor fora do topo", with_value("ALLOWED", json.dumps(
        ["ghcr.io/bipoxdxd/goblinshift-executor@" + OTHER_DIGEST, EXECUTOR])),
     "17", FALHA, frozenset()),
    ("allowlist sem o executor anterior",
     with_value("EXEC_ANTERIOR", "ghcr.io/bipoxdxd/goblinshift-executor@" + OTHER_DIGEST),
     "18", FALHA, frozenset()),
    ("allowlist com duplicata", with_value("ALLOWED", json.dumps([EXECUTOR, EXECUTOR])), "18",
     FALHA, frozenset()),
    ("dono da allowlist", with_value("ALLOW_META", "runner:root 600 910 1794355711"), "19",
     FALHA, frozenset()),
    ("modo da allowlist", with_value("ALLOW_META", "root:root 644 910 1794355711"), "19",
     FALHA, frozenset()),
    ("allowlist acima de 64 KiB", with_value("ALLOW_META", "root:root 600 65537 1794355711"),
     "19", FALHA, frozenset()),
    ("images_file de outro caminho",
     with_value("EXEC_CFG", "/var/lib/coolify-release/duora/executor-images.json"),
     "19", FALHA, frozenset()),
    ("allowlist escrita depois do current",
     with_value("ALLOW_META", "root:root 600 910 1794355750"), "20", FALHA, frozenset()),
    ("current escrito depois do history", with_value("CUR_MTIME", "1794355750"), "20", FALHA,
     frozenset()),
    ("history 6 s depois do current", with_value("CUR_MTIME", "1794355743"), "20", FALHA,
     frozenset()),
    ("app não importada", with_value("IMPORT_APP", ""), "21 app", FALHA, frozenset()),
    ("Id do app diferente do digest", with_value("IMPORT_APP", OTHER_DIGEST), "21 app", FALHA,
     frozenset()),
    ("Id do web diferente do digest", with_value("IMPORT_WEB", OTHER_DIGEST), "21 web", FALHA,
     frozenset()),
    ("Id do executor diferente do digest", with_value("IMPORT_EXECUTOR", OTHER_DIGEST),
     "21 executor", FALHA, frozenset()),
    ("container parado", with_container(API, 2, "exited"), "22 " + API, FALHA, frozenset()),
    ("container sem healthcheck", with_container(API, 3, "nohealth"), "22 " + API, FALHA,
     frozenset()),
    ("container com outra imagem", with_container(NGINX, 4, OTHER_DIGEST), "22 " + NGINX, FALHA,
     frozenset()),
    ("container fora do recurso Coolify", with_container(API, 5, "outro-uuid"), "22 " + API,
     FALHA, frozenset()),
    ("Config.Image de outro commit",
     with_container(API, 6, f"ghcr.io/bipoxdxd/goblinshift:release-{OTHER_COMMIT}@"
                    + APP_AMD64_DIGEST), "22 " + API, FALHA, frozenset()),
    ("Config.Image sem digest", with_container(NGINX, 6, "ghcr.io/bipoxdxd/goblinshift-web:latest"),
     "22 " + NGINX, FALHA, frozenset()),
    ("container ausente", with_container(API, 2, "ausente"), "22 " + API, FALHA, frozenset()),
    ("readyz 503", with_value("READYZ", "503"), "23", FALHA, frozenset()),
    ("readyz sem resposta", with_value("READYZ", "000"), "23", FALHA, frozenset()),
    ("log de erros escrito no run", with_value("ERRLOG_MTIME", "1794355555"), "24", FALHA,
     frozenset()),
    ("main em outro commit", with_value("MAIN_HEAD", OTHER_COMMIT), "25", FALHA, frozenset()),
    ("run falhou", with_value("RUN_CONCLUSAO", "failure"), "25", FALHA, frozenset()),
    ("run ainda rodando", with_values({"RUN_STATUS": "in_progress", "RUN_CONCLUSAO": ""}), "25",
     DESCONHECIDO, frozenset()),
]  # fmt: skip

CHECKLIST_ITEMS = {"1-2", *(str(number) for number in range(3, 26))}


class ItemFailureTests(unittest.TestCase):
    """Each invariant, violated alone, is not OK; the rest of the release stays as it was."""

    def test_each_wrong_value_fails_its_item(self) -> None:
        for case, text, item, expected, also in ITEM_FAILURES:
            with self.subTest(case=case, item=item):
                verdict = decide(text)
                self.assertEqual(outcome(verdict, item), expected)
                not_ok = {c.item for c in verdict.checks if c.outcome is not p.Outcome.OK}
                self.assertEqual(not_ok, {item} | also)
                want = p.Status.INCOERENTE if expected is FALHA else p.Status.DESCONHECIDO
                self.assertEqual(verdict.status, want)

    def test_every_checklist_item_has_a_failure_case(self) -> None:
        covered = {item.split()[0] for _, _, item, _, _ in ITEM_FAILURES}
        self.assertEqual(covered, CHECKLIST_ITEMS)

    def test_run_still_running_for_a_superseded_commit_is_a_failure(self) -> None:
        text = with_values({"MAIN_HEAD": OTHER_COMMIT, "RUN_STATUS": "in_progress"})
        self.assertEqual(outcome(decide(text), "25"), p.Outcome.FALHA)


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

    def test_remote_read_cut_after_complete_output_is_unknown(self) -> None:
        self.stub("ssh", 'cat "$STUB_DIR/remote.tsv"; exit 255')
        self.assertEqual(self.run_collector(COMMIT), 2)

    def test_failed_registry_read_is_unknown(self) -> None:
        self.stub("crane", "exit 1")
        self.assertEqual(self.run_collector(COMMIT), 2)


if __name__ == "__main__":
    unittest.main()
