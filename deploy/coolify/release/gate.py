"""Accept only successful CI of the exact main commit from this repository."""

import http.client
import json
import os
import re
import time
from typing import Any

REPOSITORIES = {
    "duora": "BipoXDXD/duora",
    "stillon": "BipoXDXD/stillon",
    "goblinshift": "BipoXDXD/GoblinShift",
}
REQUIRED = {
    "duora": {
        "backend",
        "frontend",
        "e2e",
        "images (amd64)",
        "images (arm64)",
        "ops",
        "secret-scan",
        "contract",
        "supply-chain",
    },
    "stillon": {
        "lint",
        "test (postgres:17-alpine)",
        "test (postgres:18-alpine)",
        "secrets",
        "web",
        "image",
        "e2e",
        "vps",
        "workflows",
        "publish",
    },
    "goblinshift": {
        "Verificações",
        "Segredos (gitleaks)",
        "Snapshots congelados",
        "Interface web",
        "Interface no navegador (Playwright)",
        "Contrato (Schemathesis)",
        "Executor sob gVisor",
        "Jornadas reais sob gVisor (Playwright)",
        "SLOs e alertas (OpenObserve)",
        "Imagem",
        "Imagem do executor",
        "Imagem do Nginx com a SPA",
        "Scripts do host (Python 3.12)",
        "Imagem e componentes com tag main",
        "Deploy na VPS",
        "Jornadas na VPS (pós-deploy)",
    },
}


def validate(run: Any, jobs: Any, project: Any, commit: Any) -> Any:
    if not isinstance(commit, str) or not re.fullmatch("[0-9a-f]{40}", commit):
        raise ValueError("Full lowercase SHA required")
    if (
        run.get("head_sha") != commit
        or run.get("head_branch") != "main"
        or run.get("event") != "push"
        or run.get("path") != ".github/workflows/ci.yml"
        or run.get("head_repository", {}).get("full_name") != REPOSITORIES[project]
        or run.get("status") != "completed"
        or run.get("conclusion") != "success"
    ):
        raise ValueError("Only successful CI main push from this repository qualifies")
    names = [job.get("name") for job in jobs]
    if len(names) != len(set(names)) or set(names) != REQUIRED[project]:
        raise ValueError("Missing, extra or duplicated CI gate")
    for job in jobs:
        if job.get("status") != "completed":
            raise ValueError("CI gate still running")
        if job.get("conclusion") != "success":
            raise ValueError("Required gate did not succeed")
    return commit


def api(path: Any) -> Any:
    connection = http.client.HTTPSConnection("api.github.com", timeout=30)
    try:
        connection.request(
            "GET",
            "/" + path,
            headers={
                "Authorization": "Bearer " + os.environ["GH_TOKEN"],
                "Accept": "application/vnd.github+json",
                "User-Agent": "coolify-release-gate",
            },
        )
        response = connection.getresponse()
        body = response.read()
        if response.status != 200:
            raise ValueError("GitHub gate API request failed")
        return json.loads(body)
    finally:
        connection.close()


def current_main(repository: str, commit: str) -> bool:
    if api(f"repos/{repository}/commits/main")["sha"] == commit:
        return True
    if os.environ.get("SKIP_SUPERSEDED") != "true":
        raise ValueError("Superseded main commit; refusing stale deployment")
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        output.write("eligible=false\n")
    print("Release superseded by newer main; no images or deployment requested")
    return False


def main() -> None:
    project = os.environ["RELEASE_PROJECT"]
    repository = REPOSITORIES[project]
    if os.environ["GITHUB_REPOSITORY"] != repository:
        raise ValueError("Repository mismatch")
    commit = os.environ.get("RELEASE_COMMIT", "")
    if not re.fullmatch("[0-9a-f]{40}", commit):
        raise ValueError("Full commit required")
    if not current_main(repository, commit):
        return
    run_id = os.environ.get("CI_RUN_ID", "")
    expected_attempt = os.environ.get("CI_RUN_ATTEMPT", "")
    event = os.environ.get("GITHUB_EVENT_NAME")
    resume_run = os.environ.get("RESUME_CI_RUN_ID", "")
    resume_attempt = os.environ.get("RESUME_CI_RUN_ATTEMPT", "")
    if resume_run or resume_attempt:
        if (
            event != "workflow_dispatch"
            or not re.fullmatch(r"[1-9][0-9]*", resume_run)
            or not re.fullmatch(r"[1-9][0-9]*", resume_attempt)
            or (run_id and run_id != resume_run)
            or (expected_attempt and expected_attempt != resume_attempt)
        ):
            raise ValueError("Recovery requires an exact CI run and attempt on manual dispatch")
        run_id, expected_attempt = resume_run, resume_attempt
    if event == "workflow_run" and not (run_id and expected_attempt):
        raise ValueError("Workflow run requires an exact CI run and attempt")
    if expected_attempt and (not run_id or not re.fullmatch(r"[1-9][0-9]*", expected_attempt)):
        raise ValueError("Invalid CI attempt binding")
    deadline = time.monotonic() + (1800 if os.environ.get("WAIT_FOR_CI") == "true" else 0)
    while True:
        if run_id:
            if not run_id.isdigit():
                raise ValueError("Invalid CI run id")
            run_path = f"repos/{repository}/actions/runs/{run_id}"
            if resume_attempt:
                run_path += f"/attempts/{resume_attempt}"
            run = api(run_path)
        else:
            runs = api(
                f"repos/{repository}/actions/workflows/ci.yml/runs?event=push&branch=main&head_sha={commit}&per_page=100"
            )["workflow_runs"]
            run = max(runs, key=lambda item: item["id"]) if runs else None
        if run is not None and run.get("status") == "completed":
            break
        if time.monotonic() >= deadline:
            raise ValueError("CI did not finish before release timeout")
        time.sleep(10)
    if (run_id and str(run["id"]) != run_id) or (
        expected_attempt and str(run["run_attempt"]) != expected_attempt
    ):
        raise ValueError("CI attempt changed after OCI artifact admission")
    result = api(
        f"repos/{repository}/actions/runs/{run['id']}/attempts/{run['run_attempt']}/jobs?per_page=100"
    )
    if result["total_count"] != len(result["jobs"]):
        raise ValueError("Incomplete CI gate response")
    validate(run, result["jobs"], project, commit)
    if not current_main(repository, commit):
        return
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        output.write(
            f"eligible=true\ncommit={commit}\nci_run={run['id']}\nci_attempt={run['run_attempt']}\n"
        )
    print(f"Exact main commit {commit}: every required CI gate succeeded")


if __name__ == "__main__":
    main()
