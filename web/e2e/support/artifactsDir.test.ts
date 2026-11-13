import { existsSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import { claimArtifactsDir, removeOwnArtifactsDir } from "./artifactsDir.ts";

const RUNNER_PID = 4242;
const WORKER_PID = 4343;
const created: string[] = [];

afterEach(() => {
  for (const dir of created.splice(0)) {
    rmSync(dir, { recursive: true, force: true });
  }
});

function claim(env: NodeJS.ProcessEnv, pid = RUNNER_PID): string {
  const dir = claimArtifactsDir(env, pid);
  created.push(dir);
  return dir;
}

describe("claimArtifactsDir", () => {
  it("cria a pasta no TMPDIR e a publica para os workers", () => {
    const env: NodeJS.ProcessEnv = {};

    const dir = claim(env);

    expect(existsSync(dir)).toBe(true);
    expect(dir.startsWith(join(tmpdir(), "goblinshift-e2e-"))).toBe(true);
    expect(env["E2E_ARTIFACTS_DIR"]).toBe(dir);
  });

  it("o worker herda a pasta do processo principal sem criar outra", () => {
    const env: NodeJS.ProcessEnv = {};
    const runnerDir = claim(env);

    expect(claim(env, WORKER_PID)).toBe(runnerDir);
  });
});

describe("removeOwnArtifactsDir", () => {
  it("o processo que criou a pasta a apaga no teardown", () => {
    const env: NodeJS.ProcessEnv = {};
    const dir = claim(env);

    removeOwnArtifactsDir(env, RUNNER_PID);

    expect(existsSync(dir)).toBe(false);
  });

  it("outro processo não apaga a pasta alheia", () => {
    const env: NodeJS.ProcessEnv = {};
    const dir = claim(env);

    removeOwnArtifactsDir(env, WORKER_PID);

    expect(existsSync(dir)).toBe(true);
  });

  it("pasta recebida pronta pelo ambiente fica para quem a passou", () => {
    const external = mkdtempSync(join(tmpdir(), "gob-e2e-externa-"));
    created.push(external);
    const env: NodeJS.ProcessEnv = { E2E_ARTIFACTS_DIR: external };

    claim(env);
    removeOwnArtifactsDir(env, RUNNER_PID);

    expect(existsSync(external)).toBe(true);
  });

  it("sem pasta, ou já apagada, não falha", () => {
    const env: NodeJS.ProcessEnv = {};
    claim(env);
    removeOwnArtifactsDir(env, RUNNER_PID);

    expect(() => {
      removeOwnArtifactsDir(env, RUNNER_PID);
      removeOwnArtifactsDir({}, RUNNER_PID);
    }).not.toThrow();
  });
});
