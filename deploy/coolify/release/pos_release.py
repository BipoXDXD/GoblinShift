"""Decide whether a GoblinShift release is coherent from facts already collected.

Pure: reads `key<TAB>value` lines (see pos_release.sh) and the merge SHA, never touches the
network or the VPS. A key that the collector could not read is simply absent, so every
invariant that needs it becomes DESCONHECIDO, never OK. The invariant numbers follow the
post-release checklist; the receiver order in receiver.py is what defines them.

Exit codes: 0 COERENTE, 1 INCOERENTE, 2 DESCONHECIDO, 3 EM ANDAMENTO.
"""

import json
import re
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from enum import Enum
from typing import Any

PROJECT = "goblinshift"
PROTOCOL_VERSION = "2"
ROLES = ("app", "web", "executor")
IMAGE_PATTERNS = {
    "app": r"ghcr\.io/bipoxdxd/goblinshift@(sha256:[0-9a-f]{64})",
    "web": r"ghcr\.io/bipoxdxd/goblinshift-web@(sha256:[0-9a-f]{64})",
    "executor": r"ghcr\.io/bipoxdxd/goblinshift-executor@(sha256:[0-9a-f]{64})",
}
SHA256_HEX = re.compile(r"[0-9a-f]{64}")
COMMIT_SHA = re.compile(r"[0-9a-f]{40}")
ALLOWLIST_PATH = "/var/lib/coolify-release/goblinshift/executor-images.json"
ALLOWLIST_OWNER = "root:root"
ALLOWLIST_MODE = "600"
ALLOWLIST_MAX_BYTES = 64 * 1024
HISTORY_EPOCH_SLACK_S = 5
READY_STATUS = "200"
REPEATABLE_KEYS = frozenset({"CONTAINER"})


class Status(Enum):
    COERENTE = 0
    INCOERENTE = 1
    DESCONHECIDO = 2
    EM_ANDAMENTO = 3

    @property
    def label(self) -> str:
        return self.name.replace("_", " ")


class Outcome(Enum):
    OK = "OK"
    FALHA = "FALHA"
    DESCONHECIDO = "DESCONHECIDO"


@dataclass(frozen=True)
class Check:
    item: str
    outcome: Outcome
    detail: str


@dataclass(frozen=True)
class Verdict:
    status: Status
    checks: tuple[Check, ...]


class MissingFactError(Exception):
    """The collector could not read this fact; the invariant cannot be decided."""


class Facts:
    def __init__(self, entries: dict[str, list[str]]) -> None:
        self._entries = entries

    def has(self, key: str) -> bool:
        return key in self._entries

    def one(self, key: str) -> str:
        values = self._entries.get(key, [])
        if len(values) != 1:
            raise MissingFactError(f"{key}: {len(values)} valor(es)")
        return values[0]

    def all(self, key: str) -> list[str]:
        return list(self._entries.get(key, []))

    def integer(self, key: str) -> int:
        value = self.one(key)
        if not re.fullmatch(r"-?[0-9]+", value):
            raise MissingFactError(f"{key} não é inteiro")
        return int(value)

    def json(self, key: str) -> Any:
        try:
            return json.loads(self.one(key))
        except json.JSONDecodeError as error:
            raise MissingFactError(f"{key} não é JSON") from error


def parse_facts(text: str) -> Facts:
    """Every line is `KEY<TAB>value`; anything else means the collection is corrupt."""
    entries: dict[str, list[str]] = {}
    for line in text.splitlines():
        if not line:
            continue
        key, separator, value = line.partition("\t")
        if not separator or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            raise ValueError("linha de coleta malformada")
        entries.setdefault(key, []).append(value.strip())
    return Facts(entries)


def evaluate(commit: str, facts: Facts) -> Verdict:
    if not COMMIT_SHA.fullmatch(commit):
        return Verdict(Status.DESCONHECIDO, (Check("0", Outcome.DESCONHECIDO, "SHA inválido"),))
    if facts.has("PENDING"):
        pending = ", ".join(facts.all("PENDING"))
        return Verdict(Status.EM_ANDAMENTO, (Check("1-2", Outcome.FALHA, f"pending: {pending}"),))
    checks = tuple(check for rule in RULES for check in _run(rule, commit, facts))
    return Verdict(_status(checks), checks)


def _status(checks: tuple[Check, ...]) -> Status:
    outcomes = {check.outcome for check in checks}
    if Outcome.FALHA in outcomes:
        return Status.INCOERENTE
    if Outcome.DESCONHECIDO in outcomes:
        return Status.DESCONHECIDO
    return Status.COERENTE


Rule = Callable[[str, Facts], Iterator[tuple[str, bool, str]]]


def _run(rule: Rule, commit: str, facts: Facts) -> list[Check]:
    results: list[Check] = []
    try:
        for item, passed, detail in rule(commit, facts):
            results.append(Check(item, Outcome.OK if passed else Outcome.FALHA, detail))
    except MissingFactError as missing:
        results.append(
            Check(rule.__name__.removeprefix("rule_"), Outcome.DESCONHECIDO, str(missing))
        )
    return results


def _release_digests(facts: Facts) -> dict[str, str]:
    images = facts.json("IMAGES")
    if not isinstance(images, dict):
        return {}
    digests = {}
    for role, pattern in IMAGE_PATTERNS.items():
        match = re.fullmatch(pattern, str(images.get(role, "")))
        if match:
            digests[role] = match.group(1)
    return digests


def rule_1_2(commit: str, facts: Facts) -> Iterator[tuple[str, bool, str]]:
    facts.one("PENDING_LIDO")
    yield "1-2", True, "sem pending.json do host nem do projeto"


def rule_3(commit: str, facts: Facts) -> Iterator[tuple[str, bool, str]]:
    others = facts.one("OUTROS_PENDING")
    yield "3", others == "", f"pending em outros projetos: {others or 'nenhum'}"


def rule_4(commit: str, facts: Facts) -> Iterator[tuple[str, bool, str]]:
    incoming = facts.one("INCOMING")
    yield "4", incoming == "", f"incoming-*: {incoming or 'nenhum'}"


def rule_5(commit: str, facts: Facts) -> Iterator[tuple[str, bool, str]]:
    locks = {key: facts.one(key) for key in ("LOCK_HOST", "LOCK_PROJETO")}
    if any(state not in ("livre", "preso") for state in locks.values()):
        raise MissingFactError("estado de lock desconhecido")
    yield "5", all(state == "livre" for state in locks.values()), f"locks {locks}"


def rule_6(commit: str, facts: Facts) -> Iterator[tuple[str, bool, str]]:
    active = facts.one("RECEPTOR_ATIVO")
    yield "6", active == "", f"receptor ativo: {active or 'nenhum'}"


def rule_7(commit: str, facts: Facts) -> Iterator[tuple[str, bool, str]]:
    current = facts.one("COMMIT")
    yield "7", current == commit, f"current.json → {current}"


def rule_8(commit: str, facts: Facts) -> Iterator[tuple[str, bool, str]]:
    project, version = facts.one("PROJETO"), facts.one("VERSAO")
    yield "8", (project, version) == (PROJECT, PROTOCOL_VERSION), f"{project} {version}"


def rule_9(commit: str, facts: Facts) -> Iterator[tuple[str, bool, str]]:
    hashes = [facts.one(key) for key in ("RECV_SHA", "RECV_INST", "RECV_REPO")]
    same = len(set(hashes)) == 1 and SHA256_HEX.fullmatch(hashes[0]) is not None
    yield "9", same, "recibo={} instalado={} repo={}".format(*hashes)


def rule_10(commit: str, facts: Facts) -> Iterator[tuple[str, bool, str]]:
    images = facts.json("IMAGES")
    exact_roles = isinstance(images, dict) and sorted(images) == sorted(ROLES)
    yield "10", exact_roles and len(_release_digests(facts)) == len(ROLES), f"images={images}"


def rule_11(commit: str, facts: Facts) -> Iterator[tuple[str, bool, str]]:
    digests = _release_digests(facts)
    for role in ROLES:
        published = _published_amd64(facts, role)
        got = digests.get(role)
        yield (
            f"11 {role}",
            got is not None and got == published,
            f"recibo={got} publicado={published}",
        )


def _published_amd64(facts: Facts, role: str) -> str | None:
    manifest = facts.json(f"MANIFEST_{role.upper()}")
    if not isinstance(manifest, dict):
        return None
    if "manifests" not in manifest:
        return facts.one(f"TAG_DIGEST_{role.upper()}")
    entries = manifest["manifests"]
    if not isinstance(entries, list):
        return None
    amd64 = [
        entry.get("digest")
        for entry in entries
        if isinstance(entry, dict)
        and isinstance(entry.get("platform"), dict)
        and entry["platform"].get("os") == "linux"
        and entry["platform"].get("architecture") == "amd64"
    ]
    return amd64[0] if len(amd64) == 1 else None


def rule_12(commit: str, facts: Facts) -> Iterator[tuple[str, bool, str]]:
    uuid = facts.one("DEPLOY_UUID")
    yield "12", uuid not in ("", "null"), f"deployment_uuid={uuid or 'vazio'}"


def rule_13(commit: str, facts: Facts) -> Iterator[tuple[str, bool, str]]:
    finished, run_created = facts.integer("FINISHED"), facts.integer("RUN_CRIADO")
    yield "13", finished > run_created, f"finished_at={finished} run={run_created}"


def rule_14(commit: str, facts: Facts) -> Iterator[tuple[str, bool, str]]:
    top, count = facts.one("HIST_TOPO"), facts.integer("HIST_N_COMMIT")
    newest = _history_epoch(commit, top) is not None
    yield "14", newest and count == 1, f"topo={top or 'nenhum'} entradas do commit={count}"


def _history_epoch(commit: str, name: str) -> int | None:
    match = re.fullmatch(rf"{commit}-([0-9]+)\.json", name)
    return int(match.group(1)) if match else None


def rule_15(commit: str, facts: Facts) -> Iterator[tuple[str, bool, str]]:
    yield "15", facts.one("HIST_IGUAL") == "true", "history mais recente == current.json"


def rule_16(commit: str, facts: Facts) -> Iterator[tuple[str, bool, str]]:
    epoch = _history_epoch(commit, facts.one("HIST_TOPO"))
    finished = facts.integer("FINISHED")
    delta = None if epoch is None else epoch - finished
    within = delta is not None and 0 <= delta <= HISTORY_EPOCH_SLACK_S
    yield "16", within, f"epoch do history - finished_at = {delta}"


def _allowlist(facts: Facts) -> list[Any]:
    allowed = facts.json("ALLOWED")
    if not isinstance(allowed, list):
        raise MissingFactError("ALLOWED não é lista")
    return allowed


def rule_17(commit: str, facts: Facts) -> Iterator[tuple[str, bool, str]]:
    allowed = _allowlist(facts)
    images = facts.json("IMAGES")
    executor = images.get("executor") if isinstance(images, dict) else None
    top = allowed[0] if allowed else None
    yield "17", executor is not None and top == executor, f"topo={top}"


def rule_18(commit: str, facts: Facts) -> Iterator[tuple[str, bool, str]]:
    allowed = _allowlist(facts)
    previous = facts.one("EXEC_ANTERIOR")
    unique = len(allowed) == len({json.dumps(entry) for entry in allowed})
    kept = previous == "" or previous in allowed
    yield "18", unique and kept, f"sem duplicata={unique} anterior presente={kept}"


def rule_19(commit: str, facts: Facts) -> Iterator[tuple[str, bool, str]]:
    owner, mode, size, _mtime = _allowlist_meta(facts)
    config = facts.one("EXEC_CFG")
    administrative = (
        owner == ALLOWLIST_OWNER
        and mode == ALLOWLIST_MODE
        and size <= ALLOWLIST_MAX_BYTES
        and config == ALLOWLIST_PATH
    )
    yield "19", administrative, f"{owner} {mode} {size} bytes images_file={config}"


def _allowlist_meta(facts: Facts) -> tuple[str, str, int, int]:
    parts = facts.one("ALLOW_META").split()
    if len(parts) != 4 or not (parts[2].isdigit() and parts[3].isdigit()):
        raise MissingFactError("ALLOW_META malformado")
    return parts[0], parts[1], int(parts[2]), int(parts[3])


def rule_20(commit: str, facts: Facts) -> Iterator[tuple[str, bool, str]]:
    allow_mtime, current_mtime = _allowlist_meta(facts)[3], facts.integer("CUR_MTIME")
    yield (
        "20",
        allow_mtime <= current_mtime,
        f"executor-images={allow_mtime} current={current_mtime}",
    )


def rule_21(commit: str, facts: Facts) -> Iterator[tuple[str, bool, str]]:
    for role in ROLES:
        image_id = facts.one(f"IMPORT_{role.upper()}")
        yield (
            f"21 {role}",
            image_id != "",
            f"release-{commit[:12]}@digest → {image_id or 'ausente'}",
        )


def rule_22(commit: str, facts: Facts) -> Iterator[tuple[str, bool, str]]:
    containers = facts.all("CONTAINER")
    if not containers:
        raise MissingFactError("nenhum container lido")
    uuid, digests = facts.one("UUID"), _release_digests(facts)
    for line in containers:
        parts = line.split("|")
        if len(parts) != 6:
            raise MissingFactError("CONTAINER malformado")
        name, role, state, health, image, label = parts
        want = digests.get(role)
        healthy = (state, health) == ("running", "healthy")
        right = want is not None and image == want and label == uuid
        yield f"22 {name}", healthy and right, f"{state} {health} {image} (quer {want})"


def rule_23(commit: str, facts: Facts) -> Iterator[tuple[str, bool, str]]:
    code = facts.one("READYZ")
    yield "23", code == READY_STATUS, f"/readyz {code}"


def rule_24(commit: str, facts: Facts) -> Iterator[tuple[str, bool, str]]:
    log_mtime, run_created = facts.integer("ERRLOG_MTIME"), facts.integer("RUN_CRIADO")
    yield "24", log_mtime < run_created, f"log de erros={log_mtime} run={run_created}"


def rule_25(commit: str, facts: Facts) -> Iterator[tuple[str, bool, str]]:
    head = facts.one("MAIN_HEAD")
    run = (facts.one("RUN_STATUS"), facts.one("RUN_CONCLUSAO"))
    passed = head == commit and run == ("completed", "success")
    yield "25", passed, f"main={head} run={run[0]}/{run[1] or '-'} (superado? veja o item 7)"


RULES: tuple[Rule, ...] = (
    rule_1_2, rule_3, rule_4, rule_5, rule_6, rule_7, rule_8, rule_9, rule_10, rule_11, rule_12,
    rule_13, rule_14, rule_15, rule_16, rule_17, rule_18, rule_19, rule_20, rule_21, rule_22,
    rule_23, rule_24, rule_25,
)  # fmt: skip


def render(verdict: Verdict) -> str:
    lines = [f"{check.outcome.value:<12} {check.item} {check.detail}" for check in verdict.checks]
    lines.append(verdict.status.label)
    return "\n".join(lines)


def main(argv: list[str], stdin: str) -> int:
    if len(argv) != 2:
        print("uso: pos_release.py <merge-sha> < fatos.tsv", file=sys.stderr)
        return Status.DESCONHECIDO.value
    try:
        facts = parse_facts(stdin)
    except ValueError as error:
        print(f"{Status.DESCONHECIDO.label}: {error}")
        return Status.DESCONHECIDO.value
    verdict = evaluate(argv[1], facts)
    print(render(verdict))
    return verdict.status.value


if __name__ == "__main__":
    sys.exit(main(sys.argv, sys.stdin.read()))
