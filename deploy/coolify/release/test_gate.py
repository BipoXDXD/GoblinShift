import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import gate

SHA = "a" * 40


class GateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ci_run = {
            "head_sha": SHA,
            "head_branch": "main",
            "event": "push",
            "path": ".github/workflows/ci.yml",
            "head_repository": {"full_name": "BipoXDXD/stillon"},
            "status": "completed",
            "conclusion": "success",
        }
        self.jobs = [
            {"name": name, "status": "completed", "conclusion": "success"}
            for name in gate.REQUIRED["stillon"]
        ]

    def test_all_exact_jobs_success(self) -> None:
        self.assertEqual(gate.validate(self.ci_run, self.jobs, "stillon", SHA), SHA)

    def test_wrong_run_ref_repository_or_state_refused(self) -> None:
        for field, value in [
            ("head_sha", "b" * 40),
            ("head_branch", "feature"),
            ("event", "pull_request"),
            ("path", ".github/workflows/fake.yml"),
            ("conclusion", "failure"),
            ("head_repository", {"full_name": "fork/stillon"}),
            ("status", "in_progress"),
        ]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                gate.validate({**self.ci_run, field: value}, self.jobs, "stillon", SHA)

    def test_missing_duplicate_skipped_failed_or_running_gate_refused(self) -> None:
        for jobs in [
            self.jobs[:-1],
            [*self.jobs, self.jobs[0]],
            [{**self.jobs[0], "conclusion": "skipped"}, *self.jobs[1:]],
            [{**self.jobs[0], "conclusion": "failure"}, *self.jobs[1:]],
            [{**self.jobs[0], "status": "in_progress"}, *self.jobs[1:]],
        ]:
            with self.assertRaises(ValueError):
                gate.validate(self.ci_run, jobs, "stillon", SHA)

    def test_sha_not_interpreted_as_arguments_or_newline(self) -> None:
        for sha in ["a" * 7, "--help", "a" * 40 + "\n"]:
            with self.assertRaises(ValueError):
                gate.validate(self.ci_run, self.jobs, "stillon", sha)


class GoblinShiftGateTests(unittest.TestCase):
    GVISOR_JOURNEYS = "Jornadas reais sob gVisor (Playwright)"

    def setUp(self) -> None:
        self.ci_run = {
            "head_sha": SHA,
            "head_branch": "main",
            "event": "push",
            "path": ".github/workflows/ci.yml",
            "head_repository": {"full_name": "BipoXDXD/GoblinShift"},
            "status": "completed",
            "conclusion": "success",
        }
        self.jobs = [
            {"name": name, "status": "completed", "conclusion": "success"}
            for name in gate.REQUIRED["goblinshift"]
        ]

    def test_all_jobs_with_browser_journeys_under_gvisor_succeed(self) -> None:
        self.assertEqual(gate.validate(self.ci_run, self.jobs, "goblinshift", SHA), SHA)

    def test_release_requires_real_results_from_both_legacy_contexts(self) -> None:
        migrated = {"Deploy na VPS", "Jornadas na VPS (pós-deploy)"}
        self.assertEqual(gate.validate(self.ci_run, self.jobs, "goblinshift", SHA), SHA)
        for name in migrated:
            for conclusion in ("skipped", "cancelled", "failure"):
                jobs = [
                    {**job, "conclusion": conclusion} if job["name"] == name else job
                    for job in self.jobs
                ]
                with self.subTest(name=name, conclusion=conclusion), self.assertRaises(ValueError):
                    gate.validate(self.ci_run, jobs, "goblinshift", SHA)

    def test_workflow_run_requires_attempt_and_refuses_a_newer_attempt(self) -> None:
        env = {
            "RELEASE_PROJECT": "goblinshift",
            "GITHUB_REPOSITORY": "BipoXDXD/GoblinShift",
            "RELEASE_COMMIT": SHA,
            "GITHUB_EVENT_NAME": "workflow_run",
            "CI_RUN_ID": "44",
        }
        for attempt, error in [
            ("", "Workflow run requires an exact CI run and attempt"),
            ("1", "CI attempt changed after OCI artifact admission"),
        ]:
            with (
                self.subTest(attempt=attempt),
                patch.dict(os.environ, {**env, "CI_RUN_ATTEMPT": attempt}, clear=True),
                patch.object(gate, "current_main", return_value=True),
                patch.object(
                    gate, "api", return_value={**self.ci_run, "id": 44, "run_attempt": 2}
                ) as api,
                self.assertRaisesRegex(ValueError, error),
            ):
                try:
                    gate.main()
                finally:
                    self.assertEqual(api.call_count, int(bool(attempt)))

    def test_exact_attempt_is_used_in_jobs_query_and_receipt(self) -> None:
        run = {**self.ci_run, "id": 44, "run_attempt": 2}
        jobs = {"total_count": len(self.jobs), "jobs": self.jobs}
        for event in ("workflow_run", "workflow_dispatch"):
            with self.subTest(event=event), tempfile.TemporaryDirectory() as tmp:
                output = Path(tmp) / "output"
                env = {
                    "RELEASE_PROJECT": "goblinshift",
                    "GITHUB_REPOSITORY": "BipoXDXD/GoblinShift",
                    "RELEASE_COMMIT": SHA,
                    "GITHUB_EVENT_NAME": event,
                    "GITHUB_OUTPUT": str(output),
                }
                if event == "workflow_run":
                    env.update(CI_RUN_ID="44", CI_RUN_ATTEMPT="2")
                    run_result = run
                else:
                    run_result = {"workflow_runs": [run]}
                with (
                    patch.dict(os.environ, env, clear=True),
                    patch.object(gate, "current_main", return_value=True),
                    patch.object(gate, "api", side_effect=[run_result, jobs]) as api,
                ):
                    gate.main()
                self.assertEqual(
                    api.call_args.args[0],
                    "repos/BipoXDXD/GoblinShift/actions/runs/44/attempts/2/jobs?per_page=100",
                )
                self.assertIn("ci_run=44\nci_attempt=2\n", output.read_text())

    def test_manual_recovery_pins_the_historical_successful_ci_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "output"
            env = {
                "RELEASE_PROJECT": "goblinshift",
                "GITHUB_REPOSITORY": "BipoXDXD/GoblinShift",
                "RELEASE_COMMIT": SHA,
                "GITHUB_EVENT_NAME": "workflow_dispatch",
                "RESUME_CI_RUN_ID": "44",
                "RESUME_CI_RUN_ATTEMPT": "1",
                "GITHUB_OUTPUT": str(output),
            }
            attempt = {**self.ci_run, "id": 44, "run_attempt": 1}
            jobs = {"total_count": len(self.jobs), "jobs": self.jobs}
            endpoint = "repos/BipoXDXD/GoblinShift/actions/runs/44/attempts/1"

            def api(path: str) -> dict[str, object]:
                if path == endpoint:
                    return attempt
                if path == f"{endpoint}/jobs?per_page=100":
                    return jobs
                self.fail(f"Recovery queried a different CI attempt: {path}")

            with (
                patch.dict(os.environ, env, clear=True),
                patch.object(gate, "current_main", return_value=True),
                patch.object(gate, "api", side_effect=api),
            ):
                gate.main()
            self.assertIn("ci_run=44\nci_attempt=1\n", output.read_text())

    def test_browser_journeys_under_gvisor_cannot_be_missing_or_skipped(self) -> None:
        others = [job for job in self.jobs if job["name"] != self.GVISOR_JOURNEYS]
        skipped = {
            "name": self.GVISOR_JOURNEYS,
            "status": "completed",
            "conclusion": "skipped",
        }
        for jobs in [others, [*others, skipped]]:
            with self.subTest(jobs=len(jobs)), self.assertRaises(ValueError):
                gate.validate(self.ci_run, jobs, "goblinshift", SHA)


class SupersededReleaseTests(unittest.TestCase):
    def test_superseded_release_is_successful_noop_before_any_ci_query(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "output"
            env = {
                "RELEASE_PROJECT": "goblinshift",
                "GITHUB_REPOSITORY": "BipoXDXD/GoblinShift",
                "RELEASE_COMMIT": SHA,
                "GITHUB_OUTPUT": str(output),
                "SKIP_SUPERSEDED": "true",
            }
            with (
                patch.dict(os.environ, env, clear=True),
                patch.object(gate, "api", return_value={"sha": "b" * 40}) as api,
            ):
                gate.main()
            self.assertIn("eligible=false", output.read_text())
            self.assertEqual(api.call_count, 1)

    def test_strict_callers_still_refuse_stale_commits(self) -> None:
        with (
            patch.dict(
                os.environ,
                {
                    "RELEASE_PROJECT": "goblinshift",
                    "GITHUB_REPOSITORY": "BipoXDXD/GoblinShift",
                    "RELEASE_COMMIT": SHA,
                },
                clear=True,
            ),
            patch.object(gate, "api", return_value={"sha": "b" * 40}),
            self.assertRaises(ValueError),
        ):
            gate.main()


if __name__ == "__main__":
    unittest.main()
