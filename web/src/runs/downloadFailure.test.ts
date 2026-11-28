import { describe, expect, it } from "vitest";

import { downloadFailureCode } from "./downloadFailure";

describe("downloadFailureCode", () => {
  it.each([
    ["GONE", "GONE"],
    ["RATE_LIMITED", "RATE_LIMITED"],
    ["gone", null],
    ["<script>", null],
    ["", null],
  ])("downloadFailureCode(%j) é %j", (value, expected) => {
    expect(downloadFailureCode(new URLSearchParams({ falha: value }))).toBe(expected);
  });

  it("sem o parâmetro devolve null", () => {
    expect(downloadFailureCode(new URLSearchParams())).toBeNull();
  });
});
