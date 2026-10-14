import { describe, expect, it } from "vitest";

import { CrossOriginUrlError, sameOriginUrl } from "./sameOrigin";

const ORIGIN = "https://goblinshift.dev";

describe("sameOriginUrl", () => {
  it.each([
    ["/api/v1/uploads/1/content", "https://goblinshift.dev/api/v1/uploads/1/content"],
    ["https://goblinshift.dev/api/v1/runs", "https://goblinshift.dev/api/v1/runs"],
    ["api/v1/runs?x=1", "https://goblinshift.dev/api/v1/runs?x=1"],
  ])("resolve %j na própria origem", (url, expected) => {
    expect(sameOriginUrl(url, ORIGIN).href).toBe(expected);
  });

  it.each([
    "https://evil.example/api/v1/uploads/1/content",
    "//evil.example/api/v1/uploads/1/content",
    "http://goblinshift.dev/api/v1/runs",
    "https://goblinshift.dev:8443/api/v1/runs",
    "https://goblinshift.dev.evil.example/api",
    "/\\evil.example/api",
    "javascript:alert(1)",
    "http://[::1",
  ])("recusa %j com erro tipado", (url) => {
    expect(() => sameOriginUrl(url, ORIGIN)).toThrow(CrossOriginUrlError);
  });
});
