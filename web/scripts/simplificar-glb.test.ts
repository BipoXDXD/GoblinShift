import { Document, NodeIO } from "@gltf-transform/core";
import { MeshoptSimplifier } from "meshoptimizer";
import { beforeAll, describe, expect, it } from "vitest";

import { simplifierReady, simplifyGlb, simplifyMesh, type MeshArrays } from "./simplificar-glb";

/** Lado da grade de teste, em vértices: 41 × 41 = 1681 vértices e 3200 triângulos. */
const GRID = 41;
/**
 * Desvio máximo de UV aceito: ~4 texels numa textura de 2048 px. O defeito da coxa (a malha
 * amostrando o cabelo) vinha de desvios de centenas de texels.
 */
const UV_TOLERANCE = 0.002;
/** Desvio máximo da normal interpolada, em comprimento do vetor (~6°). */
const NORMAL_TOLERANCE = 0.1;

/**
 * Plano plano no XY, como a bochecha vista de perto: a posição sozinha deixa colapsar tudo em
 * dois triângulos. `uv` e `normal` dizem o que o plano carrega e o simplify precisa preservar.
 */
function flatGrid(
  uv: (x: number, y: number) => [number, number],
  normal: (x: number, y: number) => [number, number, number],
): MeshArrays {
  const count = GRID * GRID;
  const positions = new Float32Array(count * 3);
  const normals = new Float32Array(count * 3);
  const uvs = new Float32Array(count * 2);
  for (let row = 0; row < GRID; row += 1) {
    for (let col = 0; col < GRID; col += 1) {
      const i = row * GRID + col;
      const x = col / (GRID - 1);
      const y = row / (GRID - 1);
      positions.set([x, y, 0], i * 3);
      normals.set(normal(x, y), i * 3);
      uvs.set(uv(x, y), i * 2);
    }
  }
  const indices: number[] = [];
  for (let row = 0; row < GRID - 1; row += 1) {
    for (let col = 0; col < GRID - 1; col += 1) {
      const a = row * GRID + col;
      indices.push(a, a + 1, a + GRID, a + 1, a + GRID + 1, a + GRID);
    }
  }
  return { indices: new Uint32Array(indices), positions, normals, uvs };
}

const curvedUv = (x: number, y: number): [number, number] => [x * x, y];
const upNormal = (): [number, number, number] => [0, 0, 1];
function bumpNormal(x: number, y: number): [number, number, number] {
  const nx = 0.5 * Math.sin(Math.PI * 2 * x);
  const ny = 0.5 * Math.sin(Math.PI * 2 * y);
  const length = Math.hypot(nx, ny, 1);
  return [nx / length, ny / length, 1 / length];
}

/**
 * Maior diferença entre o atributo original de cada vértice da grade e o atributo que a malha
 * simplificada interpola no mesmo ponto do plano.
 */
function maxInterpolationError(
  source: MeshArrays,
  simplified: MeshArrays,
  attribute: "uvs" | "normals",
): number {
  const size = attribute === "uvs" ? 2 : 3;
  let worst = 0;
  for (let v = 0; v < source.positions.length / 3; v += 1) {
    const px = source.positions[v * 3] ?? 0;
    const py = source.positions[v * 3 + 1] ?? 0;
    const interpolated = interpolateAt(simplified, attribute, size, px, py);
    let squared = 0;
    for (let c = 0; c < size; c += 1) {
      const delta = (interpolated[c] ?? 0) - (source[attribute][v * size + c] ?? 0);
      squared += delta * delta;
    }
    worst = Math.max(worst, Math.sqrt(squared));
  }
  return worst;
}

const BARYCENTRIC_SLACK = 1e-6;

function interpolateAt(
  mesh: MeshArrays,
  attribute: "uvs" | "normals",
  size: number,
  px: number,
  py: number,
): number[] {
  const at = (vertex: number, c: number): number => mesh.positions[vertex * 3 + c] ?? 0;
  for (let t = 0; t < mesh.indices.length; t += 3) {
    const [a, b, c] = [mesh.indices[t] ?? 0, mesh.indices[t + 1] ?? 0, mesh.indices[t + 2] ?? 0];
    const det =
      (at(b, 1) - at(c, 1)) * (at(a, 0) - at(c, 0)) + (at(c, 0) - at(b, 0)) * (at(a, 1) - at(c, 1));
    const wa =
      ((at(b, 1) - at(c, 1)) * (px - at(c, 0)) + (at(c, 0) - at(b, 0)) * (py - at(c, 1))) / det;
    const wb =
      ((at(c, 1) - at(a, 1)) * (px - at(c, 0)) + (at(a, 0) - at(c, 0)) * (py - at(c, 1))) / det;
    const wc = 1 - wa - wb;
    if (Math.min(wa, wb, wc) < -BARYCENTRIC_SLACK) {
      continue;
    }
    return Array.from({ length: size }, (_, k) => {
      const value = (vertex: number): number => mesh[attribute][vertex * size + k] ?? 0;
      return wa * value(a) + wb * value(b) + wc * value(c);
    });
  }
  throw new Error(`nenhum triângulo cobre (${String(px)}, ${String(py)})`);
}

beforeAll(async () => {
  await simplifierReady;
});

describe("simplifyMesh", () => {
  it("o simplify só de posição, o de antes, distorce o UV de um plano (o risco na coxa)", () => {
    const grid = flatGrid(curvedUv, upNormal);
    const [indices] = MeshoptSimplifier.simplify(grid.indices, grid.positions, 3, 0, 0.01);

    const distorted = maxInterpolationError(
      grid,
      { ...grid, indices: Uint32Array.from(indices) },
      "uvs",
    );

    expect(distorted).toBeGreaterThan(UV_TOLERANCE * 10);
  });

  it("preserva o UV de um plano com mapeamento não linear", () => {
    const grid = flatGrid(curvedUv, upNormal);

    const { mesh } = simplifyMesh(grid);

    expect(mesh.indices.length).toBeLessThan(grid.indices.length);
    expect(maxInterpolationError(grid, mesh, "uvs")).toBeLessThan(UV_TOLERANCE);
  });

  it("preserva as normais de um plano com relevo só no sombreamento (a bochecha)", () => {
    const grid = flatGrid((x, y) => [x, y], bumpNormal);

    const { mesh } = simplifyMesh(grid);

    expect(mesh.indices.length).toBeLessThan(grid.indices.length);
    expect(maxInterpolationError(grid, mesh, "normals")).toBeLessThan(NORMAL_TOLERANCE);
  });

  it("um plano sem nada a preservar cai para poucos triângulos", () => {
    const grid = flatGrid((x, y) => [x, y], upNormal);

    const { mesh } = simplifyMesh(grid);

    expect(mesh.indices.length / 3).toBeLessThanOrEqual(8);
  });

  it("compacta os vértices e mantém cada atributo junto do seu vértice", () => {
    const grid = flatGrid(curvedUv, bumpNormal);

    const { mesh } = simplifyMesh(grid);

    const vertexCount = mesh.positions.length / 3;
    expect(new Set(mesh.indices).size).toBe(vertexCount);
    expect(Math.max(...mesh.indices)).toBe(vertexCount - 1);
    for (let v = 0; v < vertexCount; v += 1) {
      const x = mesh.positions[v * 3] ?? Number.NaN;
      const y = mesh.positions[v * 3 + 1] ?? Number.NaN;
      expect(mesh.uvs[v * 2]).toBeCloseTo(x * x, 5);
      expect(mesh.normals[v * 3]).toBeCloseTo(bumpNormal(x, y)[0], 5);
    }
  });
});

describe("simplifyGlb", () => {
  async function glbWith(attributes: readonly ("NORMAL" | "TEXCOORD_0")[]): Promise<Uint8Array> {
    const grid = flatGrid(curvedUv, bumpNormal);
    const document = new Document();
    const buffer = document.createBuffer();
    const accessor = (
      type: "SCALAR" | "VEC2" | "VEC3",
      array: Uint32Array<ArrayBuffer> | Float32Array<ArrayBuffer>,
    ) => document.createAccessor().setType(type).setArray(array).setBuffer(buffer);
    const primitive = document
      .createPrimitive()
      .setIndices(accessor("SCALAR", grid.indices))
      .setAttribute("POSITION", accessor("VEC3", grid.positions));
    if (attributes.includes("NORMAL")) {
      primitive.setAttribute("NORMAL", accessor("VEC3", grid.normals));
    }
    if (attributes.includes("TEXCOORD_0")) {
      primitive.setAttribute("TEXCOORD_0", accessor("VEC2", grid.uvs));
    }
    const mesh = document.createMesh().addPrimitive(primitive);
    document.createScene().addChild(document.createNode().setMesh(mesh));
    return new NodeIO().writeBinary(document);
  }

  it("reduz a malha do GLB e conta os triângulos de antes e de depois", async () => {
    const result = await simplifyGlb(await glbWith(["NORMAL", "TEXCOORD_0"]));

    expect(result.trianglesBefore).toBe((GRID - 1) * (GRID - 1) * 2);
    expect(result.trianglesAfter).toBeLessThan(result.trianglesBefore);
    const reread = await new NodeIO().readBinary(result.glb);
    const primitive = reread.getRoot().listMeshes()[0]?.listPrimitives()[0];
    expect(primitive?.getIndices()?.getCount()).toBe(result.trianglesAfter * 3);
  });

  it.each([
    ["NORMAL", ["TEXCOORD_0"]],
    ["TEXCOORD_0", ["NORMAL"]],
  ] as const)("recusa malha sem %s, que o simplify precisa preservar", async (missing, present) => {
    await expect(simplifyGlb(await glbWith(present))).rejects.toThrow(missing);
  });
});
