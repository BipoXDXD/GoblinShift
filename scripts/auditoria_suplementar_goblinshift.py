"""Captura identidades do pytest e confere os contadores do Chromium num runner hospedado."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

MARKERS = {"needs_root", "openobserve", "executor_image", "executor_ctl"}
LANES = ("general", "root", "openobserve", "executor")
DEDICATED_COUNTS = {"root": 16, "openobserve": 98, "executor": 60}
DEDICATED = ("root", "openobserve", "executor")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def git(*args):
    return subprocess.check_output(["git", *args], text=True).strip()


def runner_identity():
    return {name: os.environ[name] for name in ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_SHA", "GITHUB_JOB")}


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2, sort_keys=True)
        file.write("\n")


def read(path):
    return json.loads(path.read_bytes())


class IdentityCapture:
    def __init__(self):
        self.items = {}
        self.deselected = []
        self.selected = []
        self.calls = []
        self.skips = []

    def pytest_itemcollected(self, item):
        require(item.nodeid not in self.items, f"Identidade duplicada: {item.nodeid}")
        self.items[item.nodeid] = sorted({mark.name for mark in item.iter_markers()} & MARKERS)

    def pytest_deselected(self, items):
        self.deselected.extend(item.nodeid for item in items)

    def pytest_collection_finish(self, session):
        self.selected = [item.nodeid for item in session.items]

    def pytest_collectreport(self, report):
        if report.skipped:
            self.skips.append(report.nodeid)

    def pytest_runtest_logreport(self, report):
        if report.skipped or hasattr(report, "wasxfail"):
            self.skips.append(report.nodeid)
        if report.when == "call":
            self.calls.append((report.nodeid, report.outcome))


def subject_for(manifest, head):
    subject, = (item for item in manifest["subjects"] if item["head"] == head)
    return subject


def capture(args):
    import pytest

    require(git("rev-parse", "HEAD") == args.head, "Head de coleta divergente.")
    subject = subject_for(read(args.manifest), args.head)
    for entry in subject["python_inputs"]:
        path = entry["path"]
        require(git("rev-parse", f"HEAD:{path}") == entry["git_oid"], f"Entrada Python divergente: {path}.")
    sys.path.insert(0, str(Path.cwd()))
    reporter = IdentityCapture()
    selectors = {
        "general": ("not openobserve and not executor_image and not needs_root", []),
        "root": ("needs_root", ["tests/executor_ctl"]),
        "openobserve": ("openobserve and not needs_root", []),
        "executor": ("(executor_ctl or executor_image) and not needs_root", []),
    }
    expression, paths = selectors[args.lane]
    if args.execute:
        require(args.lane != "general", "A suíte geral se limita à coleta.")
    flags = ["--fail-on-skip", "-p", "no:cacheprovider", "-q", "-m", expression]
    if not args.execute:
        flags.extend(["-q", "--collect-only"])
    status = pytest.main([*flags, *paths], plugins=[reporter])
    selected = sorted(reporter.selected)
    deselected = sorted(reporter.deselected)
    passed = sorted(node for node, outcome in reporter.calls if outcome == "passed")
    write(args.output, {
        "head": args.head, "runner": runner_identity(), "lane": args.lane, "execute": args.execute,
        "argv": [*flags, *paths], "exit_code": int(status), "selected": selected,
        "deselected": deselected, "markers": {
            node: reporter.items[node] for node in deselected if reporter.items[node]
        }, "passed": passed, "skips": reporter.skips,
    })
    require(status == 0 and not reporter.skips, f"Coleta ou execução inválida em {args.lane}.")
    require(len(selected) == len(set(selected)) and len(deselected) == len(set(deselected))
            and not set(selected) & set(deselected)
            and set(reporter.items) == set(selected) | set(deselected),
            f"A partição do pytest divergiu em {args.lane}.")
    expected = subject["pytest_general_passed"] if args.lane == "general" else DEDICATED_COUNTS[args.lane]
    require(len(selected) == expected, f"Total selecionado divergente em {args.lane}.")
    if args.execute:
        require(passed == selected and len(reporter.calls) == len(selected),
                f"Casos selecionados sem aprovação individual em {args.lane}.")


def reconcile(args):
    subject = subject_for(read(args.manifest), args.head)
    runner = runner_identity()
    require(git("rev-parse", "HEAD") == args.head, "Head de reconciliação divergente.")

    def lane_evidence(lane):
        evidence = read(args.directory / f"{args.head}-{lane}.json")
        require(evidence["head"] == args.head and evidence["runner"] == runner
                and evidence["lane"] == lane and evidence["execute"] == (lane != "general")
                and evidence["exit_code"] == 0 and not evidence["skips"],
                f"Evidência fora do mesmo SHA ou execução em {lane}.")
        return evidence

    general = lane_evidence("general")
    omitted = set(general["deselected"])
    universe = set(general["selected"]) | omitted
    require(len(omitted) == len(general["deselected"]) == subject["pytest_general_deselected"]
            and len(general["selected"]) == subject["pytest_general_passed"]
            and not set(general["selected"]) & omitted,
            "A partição geral diverge das contagens históricas.")
    covered_by = {}
    for lane in DEDICATED:
        evidence = lane_evidence(lane)
        require(evidence["passed"] == evidence["selected"]
                and len(set(evidence["passed"])) == len(evidence["passed"]) == DEDICATED_COUNTS[lane],
                f"Aprovações individuais inconclusivas em {lane}.")
        covered_by[lane] = set(evidence["passed"])

    coverage = {
        node: [lane for lane in DEDICATED if node in covered_by[lane]] for node in sorted(omitted)
    }
    uncovered = [node for node, lanes in coverage.items() if not lanes]
    unexpected = {lane: sorted(nodes - universe) for lane, nodes in covered_by.items()}
    write(args.output, {
        **subject, "runner": runner, "deselected": len(omitted), "coverage": coverage,
        "covered": len(coverage) - len(uncovered), "markers": general["markers"],
        "uncovered": uncovered, "dedicated_unexpected": unexpected,
        "dedicated_passed": {lane: len(nodes) for lane, nodes in covered_by.items()},
    })
    require(not uncovered and not any(unexpected.values()),
            "A reconciliação encontrou identidades desmarcadas sem execução ou fora da coleta geral.")
    print(f"{len(omitted)} identidades desmarcadas aprovadas nas suítes dedicadas do mesmo SHA.")


def chromium(args):
    manifest = read(args.manifest)
    require(git("rev-parse", "HEAD") == args.head, "Head do Chromium divergente.")
    subject = subject_for(manifest, args.head)
    require(subject["repeat_chromium"], "Este SHA já tem prova Chromium ou não aciona a suíte.")
    for path, blob in manifest["collector_blobs"].items():
        require(git("hash-object", path) == blob, f"Coletor alterado em {path}.")
    data = args.log.read_bytes()
    lines = data.decode().splitlines()
    rows = [json.loads(line) for line in lines if line.startswith('{"sabotage":')]
    require(len(rows) == len({row["sabotage"] for row in rows}) == 39, "Identidades dos cenários divergentes.")
    require(any(line == "Os 39 defeitos plantados derrubaram a spec que os vigia." for line in lines),
            "Fechamento dos 39 cenários ausente.")
    for row in rows:
        require(row["exitCode"] == 1 and row["caught"] is True
                and row["selected"] == row["executed"] == row["expected"] + row["unexpected"]
                and 0 < row["failed"] <= row["unexpected"]
                and row["skipped"] == row["flaky"] == row["errors"] == 0,
                f"Cenário incompleto: {row['sabotage']}")
    total = {key: sum(row[key] for row in rows) for key in
             ("selected", "executed", "expected", "unexpected", "failed", "skipped", "flaky", "errors")}
    write(args.output, {**subject, "runner": runner_identity(), "collector": manifest["collector"],
                       "collector_blobs": manifest["collector_blobs"], "cases": rows, "total": total,
                       "log_sha256": hashlib.sha256(data).hexdigest()})
    print(json.dumps(total, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    cap = sub.add_parser("capture")
    cap.add_argument("--head", required=True)
    cap.add_argument("--manifest", type=Path, required=True)
    cap.add_argument("--lane", required=True, choices=LANES)
    cap.add_argument("--execute", action="store_true")
    cap.add_argument("--output", type=Path, required=True)
    rec = sub.add_parser("reconcile")
    rec.add_argument("--head", required=True)
    rec.add_argument("--manifest", type=Path, required=True)
    rec.add_argument("--directory", type=Path, required=True)
    rec.add_argument("--output", type=Path, required=True)
    pw = sub.add_parser("chromium")
    pw.add_argument("--head", required=True)
    pw.add_argument("--manifest", type=Path, required=True)
    pw.add_argument("--log", type=Path, required=True)
    pw.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    {"capture": capture, "reconcile": reconcile, "chromium": chromium}[args.command](args)


if __name__ == "__main__":
    main()
