import { describe, expect, it } from "vitest";

import { assertLocalWriteTarget } from "./writeTarget.ts";

describe("assertLocalWriteTarget", () => {
  it.each([
    "http://localhost",
    "http://localhost:8080",
    "http://127.0.0.1:18080",
    "http://[::1]:80",
  ])("aceita %s", (url) => {
    expect(() => {
      assertLocalWriteTarget(url);
    }).not.toThrow();
  });

  it.each([
    "https://goblinshift.dev",
    "https://www.goblinshift.dev",
    "http://192.0.2.1",
    "http://localhost@goblinshift.dev",
    "http://localhost.goblinshift.dev",
  ])("recusa %s", (url) => {
    expect(() => {
      assertLocalWriteTarget(url);
    }).toThrow("E2E_BASE_URL precisa ser local");
  });
});
