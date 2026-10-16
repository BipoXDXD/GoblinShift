import { describe, expect, it, vi } from "vitest";

import { ApiError } from "../api/response";
import {
  loadPreviews,
  thumbnailArtifacts,
  type PreviewServices,
  type PreviewState,
} from "./thumbnails";

const rateLimited = (retryAfterMs: number | null): ApiError =>
  new ApiError({
    kind: "problem",
    status: 429,
    retryAfterMs,
    problem: {
      type: "https://goblinshift.dev/problems/rate-limited",
      title: "Muitas requisições",
      status: 429,
      detail: "Espere.",
      instance: "/api/v1/artifacts/a/download",
      code: "RATE_LIMITED",
      request_id: "0b9c3c1e-7f62-4d1c-9a49-0d6f5d0b8f3a",
    },
  });

function artifact(file_name: string, role = "output") {
  return { artifact_id: file_name, logical_name: file_name, role, file_name };
}

describe("thumbnailArtifacts", () => {
  it("só as saídas JPEG em thumbnails/, na ordem publicada", () => {
    const artifacts = [
      artifact("audio/a.wav"),
      artifact("thumbnails/a-10pct.jpg"),
      artifact("thumbnails/a-50pct.JPG"),
      artifact("thumbnails/a.png"),
      artifact("images/a.jpg"),
      artifact("thumbnails/b.jpg", "package"),
      artifact("media-mill.zip", "package"),
    ];

    expect(thumbnailArtifacts(artifacts).map((a) => a.file_name)).toEqual([
      "thumbnails/a-10pct.jpg",
      "thumbnails/a-50pct.JPG",
    ]);
  });

  it("no máximo 15 previews", () => {
    const artifacts = Array.from({ length: 16 }, (_, i) => artifact(`thumbnails/${String(i)}.jpg`));

    expect(thumbnailArtifacts(artifacts)).toHaveLength(15);
  });
});

function services(fetchJpeg: PreviewServices["fetchJpeg"]) {
  const waits: number[] = [];
  const value: PreviewServices = {
    fetchJpeg,
    createUrl: (blob) => `blob:${String(blob.size)}`,
    wait: (ms) => {
      waits.push(ms);
      return Promise.resolve();
    },
  };
  return { value, waits };
}

async function run(
  ids: readonly string[],
  value: PreviewServices,
  signal = new AbortController().signal,
) {
  const results: [string, PreviewState][] = [];
  await loadPreviews({
    artifactIds: ids,
    services: value,
    onResult: (id, state) => results.push([id, state]),
    signal,
  });
  return results;
}

describe("loadPreviews", () => {
  it("uma de cada vez, na ordem", async () => {
    let inFlight = 0;
    let maxInFlight = 0;
    const fetchJpeg = vi.fn(async (id: string) => {
      inFlight += 1;
      maxInFlight = Math.max(maxInFlight, inFlight);
      await Promise.resolve();
      inFlight -= 1;
      return new Blob([id]);
    });

    const results = await run(["a", "bb", "ccc"], services(fetchJpeg).value);

    expect(maxInFlight).toBe(1);
    expect(results).toEqual([
      ["a", { status: "ready", url: "blob:1" }],
      ["bb", { status: "ready", url: "blob:2" }],
      ["ccc", { status: "ready", url: "blob:3" }],
    ]);
  });

  it("429 espera o Retry-After e tenta de novo", async () => {
    const fetchJpeg = vi
      .fn<PreviewServices["fetchJpeg"]>()
      .mockRejectedValueOnce(rateLimited(3_000))
      .mockResolvedValueOnce(new Blob(["x"]));
    const { value, waits } = services(fetchJpeg);

    expect(await run(["a"], value)).toEqual([["a", { status: "ready", url: "blob:1" }]]);
    expect(waits).toEqual([3_000]);
  });

  it("espera tem teto e default; depois de 3 recusas desiste só daquela miniatura", async () => {
    const fetchJpeg = vi
      .fn<PreviewServices["fetchJpeg"]>()
      .mockRejectedValueOnce(rateLimited(600_000))
      .mockRejectedValueOnce(rateLimited(null))
      .mockRejectedValueOnce(rateLimited(1_000))
      .mockResolvedValueOnce(new Blob(["y"]));
    const { value, waits } = services(fetchJpeg);

    expect(await run(["a", "b"], value)).toEqual([
      ["a", { status: "failed" }],
      ["b", { status: "ready", url: "blob:1" }],
    ]);
    expect(waits).toEqual([30_000, 2_000]);
  });

  it("outra falha não tenta de novo", async () => {
    const fetchJpeg = vi
      .fn<PreviewServices["fetchJpeg"]>()
      .mockRejectedValue(new ApiError({ kind: "network" }));
    const { value, waits } = services(fetchJpeg);

    expect(await run(["a"], value)).toEqual([["a", { status: "failed" }]]);
    expect(fetchJpeg).toHaveBeenCalledTimes(1);
    expect(waits).toEqual([]);
  });

  it("depois de abortar não informa nem busca mais nada", async () => {
    const controller = new AbortController();
    const fetchJpeg = vi.fn(() => {
      controller.abort();
      return Promise.resolve(new Blob(["x"]));
    });

    const results = await run(["a", "b"], services(fetchJpeg).value, controller.signal);

    expect(results).toEqual([]);
    expect(fetchJpeg).toHaveBeenCalledTimes(1);
  });
});
