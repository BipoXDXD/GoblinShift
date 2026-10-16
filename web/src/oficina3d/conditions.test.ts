import { afterEach, describe, expect, it, vi } from "vitest";

import {
  browserEnvironment,
  isSoftwareRenderer,
  shouldLoad3D,
  type Environment3D,
} from "./conditions";

function environment(overrides: Partial<Environment3D> = {}): Environment3D {
  return {
    prefersReducedMotion: () => false,
    subscribeReducedMotion: () => () => undefined,
    savesData: () => false,
    supportsWebGL2: () => true,
    ...overrides,
  };
}

describe("shouldLoad3D", () => {
  it("carrega quando há WebGL 2 e nenhuma preferência contra", () => {
    expect(shouldLoad3D(environment())).toBe(true);
  });

  it.each<[string, Partial<Environment3D>]>([
    ["movimento reduzido", { prefersReducedMotion: () => true }],
    ["economia de dados", { savesData: () => true }],
    ["sem WebGL 2", { supportsWebGL2: () => false }],
  ])("fica só a imagem com %s", (_case, overrides) => {
    expect(shouldLoad3D(environment(overrides))).toBe(false);
  });

  it("não cria contexto WebGL quando a preferência já decidiu", () => {
    const supportsWebGL2 = vi.fn(() => true);

    shouldLoad3D(environment({ prefersReducedMotion: () => true, supportsWebGL2 }));

    expect(supportsWebGL2).not.toHaveBeenCalled();
  });
});

describe("browserEnvironment.subscribeReducedMotion", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("avisa quando a preferência de movimento muda e para de avisar ao cancelar", () => {
    const query = new EventTarget();
    const matchMedia = vi.fn(() => query);
    vi.stubGlobal("matchMedia", matchMedia);
    const onChange = vi.fn();

    const unsubscribe = browserEnvironment.subscribeReducedMotion(onChange);
    query.dispatchEvent(new Event("change"));
    unsubscribe();
    query.dispatchEvent(new Event("change"));

    expect(matchMedia).toHaveBeenCalledWith("(prefers-reduced-motion: reduce)");
    expect(onChange).toHaveBeenCalledOnce();
  });
});

describe("browserEnvironment.supportsWebGL2", () => {
  it("pede WebGL 2 sem a ressalva de desempenho e sem contexto responde não", () => {
    const getContext = vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);

    expect(browserEnvironment.supportsWebGL2()).toBe(false);
    expect(getContext).toHaveBeenCalledWith("webgl2", { failIfMajorPerformanceCaveat: true });
  });
});

describe("isSoftwareRenderer", () => {
  it.each([
    "ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (LLVM 10.0.0)), SwiftShader driver)",
    "llvmpipe (LLVM 15.0.7, 256 bits)",
    "Microsoft Basic Render Driver",
    "Software Rasterizer",
  ])("%s é WebGL em software", (renderer) => {
    expect(isSoftwareRenderer(renderer)).toBe(true);
  });

  it.each([
    "ANGLE (Apple, ANGLE Metal Renderer: Apple M3, Unspecified Version)",
    "NVIDIA GeForce RTX 4060/PCIe/SSE2",
  ])("%s é GPU", (renderer) => {
    expect(isSoftwareRenderer(renderer)).toBe(false);
  });

  it("sem o nome (navegador que o esconde), confia na ressalva de desempenho", () => {
    expect(isSoftwareRenderer(null)).toBe(false);
  });
});
