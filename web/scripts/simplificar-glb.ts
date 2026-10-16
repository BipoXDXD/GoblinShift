// Redução da malha da Rúbia para o 3D da abertura (ADR 030), chamada por
// scripts/arte/otimizar-glb.sh: `node scripts/simplificar-glb.ts ENTRADA.glb SAIDA.glb`.
//
// O `simplify` do gltf-transform só olha a posição. Na Rúbia isso achatava a bochecha, apagava a
// íris do olho esquerdo e esticava o UV de triângulos da coxa até amostrarem o cabelo (o risco
// vermelho). Aqui o simplificador do meshoptimizer mede também o desvio da normal e do UV, e a
// redução para onde algum dos três passaria do erro aceito. As costuras de UV continuam
// respeitadas como no `simplify` comum: o meshoptimizer as reconhece pelos vértices repetidos.
import { readFile, writeFile } from "node:fs/promises";
import { argv } from "node:process";
import { fileURLToPath } from "node:url";

import { NodeIO, type Primitive } from "@gltf-transform/core";
import { MeshoptSimplifier } from "meshoptimizer";

export interface MeshArrays {
  readonly indices: Uint32Array<ArrayBuffer>;
  readonly positions: Float32Array<ArrayBuffer>;
  readonly normals: Float32Array<ArrayBuffer>;
  readonly uvs: Float32Array<ArrayBuffer>;
}

/**
 * Erro aceito, relativo ao tamanho da malha, somando posição, normal e UV com os pesos abaixo.
 * Medido na Rúbia de 1 milhão de triângulos (ADR 030): 0,0015 deixa olho, bochecha e coxa iguais
 * ao bruto nos closes do three, em 240 mil triângulos; 0,002 já perde relevo no rosto, e 0,01 (o
 * valor antigo, só de posição) gerava os três defeitos.
 */
const TARGET_ERROR = 0.0015;
/** Normal unitária: peso ~1 iguala um desvio de 1 na normal a um desvio de 1 na posição. */
const NORMAL_WEIGHT = 1;
/**
 * UV de 0 a 1: com peso 16, o UV interpolado fica a menos de 0,002 (~4 texels em 2048 px) do
 * original no teste do plano. Na Rúbia, pesos 4 e 16 deram a mesma contagem de triângulos.
 */
const UV_WEIGHT = 16;
const NORMAL_SIZE = 3;
const UV_SIZE = 2;
const ATTRIBUTE_STRIDE = NORMAL_SIZE + UV_SIZE;
const ATTRIBUTE_WEIGHTS = [
  ...Array<number>(NORMAL_SIZE).fill(NORMAL_WEIGHT),
  ...Array<number>(UV_SIZE).fill(UV_WEIGHT),
];
/** Sem alvo de contagem: quem decide até onde reduzir é o erro aceito. */
const NO_TARGET_COUNT = 0;

export const simplifierReady: Promise<void> = MeshoptSimplifier.ready;

/** Reduz a malha até o erro aceito e devolve só os vértices que sobraram. Exige `simplifierReady`. */
export function simplifyMesh(mesh: MeshArrays): { mesh: MeshArrays; error: number } {
  const vertexCount = mesh.positions.length / 3;
  const attributes = new Float32Array(vertexCount * ATTRIBUTE_STRIDE);
  for (let v = 0; v < vertexCount; v += 1) {
    attributes.set(
      mesh.normals.subarray(v * NORMAL_SIZE, (v + 1) * NORMAL_SIZE),
      v * ATTRIBUTE_STRIDE,
    );
    attributes.set(
      mesh.uvs.subarray(v * UV_SIZE, (v + 1) * UV_SIZE),
      v * ATTRIBUTE_STRIDE + NORMAL_SIZE,
    );
  }
  const [indices, error] = MeshoptSimplifier.simplifyWithAttributes(
    mesh.indices,
    mesh.positions,
    3,
    attributes,
    ATTRIBUTE_STRIDE,
    ATTRIBUTE_WEIGHTS,
    null,
    NO_TARGET_COUNT,
    TARGET_ERROR,
  );
  return { mesh: compact(mesh, indices), error };
}

/** Lê o GLB, reduz cada primitiva e devolve o GLB novo com a contagem de triângulos. */
export async function simplifyGlb(
  glb: Uint8Array,
): Promise<{ glb: Uint8Array; trianglesBefore: number; trianglesAfter: number }> {
  await simplifierReady;
  const io = new NodeIO();
  const document = await io.readBinary(glb);
  let trianglesBefore = 0;
  let trianglesAfter = 0;
  for (const mesh of document.getRoot().listMeshes()) {
    for (const primitive of mesh.listPrimitives()) {
      const source = readMesh(primitive);
      const { mesh: simplified } = simplifyMesh(source);
      writeMesh(primitive, simplified);
      trianglesBefore += source.indices.length / 3;
      trianglesAfter += simplified.indices.length / 3;
    }
  }
  return { glb: await io.writeBinary(document), trianglesBefore, trianglesAfter };
}

/** Renumera os vértices usados pelos índices novos e descarta o resto dos atributos. */
function compact(mesh: MeshArrays, simplifiedIndices: Uint32Array): MeshArrays {
  const indices = Uint32Array.from(simplifiedIndices);
  const [remap, vertexCount] = MeshoptSimplifier.compactMesh(indices);
  const positions = new Float32Array(vertexCount * 3);
  const normals = new Float32Array(vertexCount * NORMAL_SIZE);
  const uvs = new Float32Array(vertexCount * UV_SIZE);
  remap.forEach((target, source) => {
    if (target === 0xffffffff) {
      return;
    }
    positions.set(mesh.positions.subarray(source * 3, source * 3 + 3), target * 3);
    normals.set(
      mesh.normals.subarray(source * NORMAL_SIZE, (source + 1) * NORMAL_SIZE),
      target * NORMAL_SIZE,
    );
    uvs.set(mesh.uvs.subarray(source * UV_SIZE, (source + 1) * UV_SIZE), target * UV_SIZE);
  });
  return { indices, positions, normals, uvs };
}

function readMesh(primitive: Primitive): MeshArrays {
  const float = (semantic: string): Float32Array<ArrayBuffer> => {
    const array = primitive.getAttribute(semantic)?.getArray();
    if (!(array instanceof Float32Array)) {
      throw new Error(`a malha precisa de ${semantic} em float, antes de qualquer quantização`);
    }
    return Float32Array.from(array);
  };
  const indices = primitive.getIndices()?.getArray();
  if (!indices) {
    throw new Error("a malha precisa de índices: rode o weld antes");
  }
  return {
    indices: Uint32Array.from(indices),
    positions: float("POSITION"),
    normals: float("NORMAL"),
    uvs: float("TEXCOORD_0"),
  };
}

function writeMesh(primitive: Primitive, mesh: MeshArrays): void {
  const set = (semantic: string, array: Float32Array<ArrayBuffer>): void => {
    primitive.getAttribute(semantic)?.setArray(array);
  };
  set("POSITION", mesh.positions);
  set("NORMAL", mesh.normals);
  set("TEXCOORD_0", mesh.uvs);
  primitive.getIndices()?.setArray(mesh.indices);
}

async function main(): Promise<void> {
  const [input, output] = argv.slice(2);
  if (input === undefined || output === undefined) {
    throw new Error("uso: node scripts/simplificar-glb.ts ENTRADA.glb SAIDA.glb");
  }
  const result = await simplifyGlb(await readFile(input));
  await writeFile(output, result.glb);
  console.log(`triângulos: ${String(result.trianglesBefore)} → ${String(result.trianglesAfter)}`);
}

if (argv[1] === fileURLToPath(import.meta.url)) {
  await main();
}
