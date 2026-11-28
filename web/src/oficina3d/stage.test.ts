import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Stage, type AnimationLoop } from "./stage";

/** Entrada da cena: 3,5 s, abaixo dos 5 s da WCAG 2.2.2. */
const INTRO_MS = 3_500;
/** Pose de repouso: o giro da imagem estática (render a 20°), em graus. */
const REST_YAW_DEGREES = "-20";

/**
 * Laço de quadros controlado pelo teste: no SwiftShader do CI um quadro chegou a levar 5 s, e o
 * teste entrega os quadros quando quer, com o relógio que quiser.
 */
class ManualLoop implements AnimationLoop {
  callback: ((time: number) => void) | null = null;

  readonly setAnimationLoop = (callback: ((time: number) => void) | null): void => {
    this.callback = callback;
  };

  frame(time: number): void {
    this.callback?.(time);
  }
}

let host: HTMLDivElement;
let loop: ManualLoop;
let stage: Stage;

beforeEach(() => {
  vi.useFakeTimers();
  host = document.createElement("div");
  loop = new ManualLoop();
  stage = new Stage(host, loop, { applyPose: () => undefined, render: () => undefined });
});

afterEach(() => {
  stage.stop();
  vi.useRealTimers();
});

describe("entrada", () => {
  it("para em 3,5 s mesmo sem nenhum quadro do laço", () => {
    stage.playIntro();
    expect(host.dataset["movimento"]).toBe("entrada");

    vi.advanceTimersByTime(INTRO_MS);

    expect(host.dataset["movimento"]).toBe("parado");
    expect(host.dataset["giro"]).toBe(REST_YAW_DEGREES);
    expect(loop.callback).toBeNull();
  });

  it("para em 3,5 s contados do começo, mesmo que o primeiro quadro chegue tarde", () => {
    const start = performance.now();
    stage.playIntro();
    vi.advanceTimersByTime(3_000);
    loop.frame(start + 3_000);
    expect(host.dataset["movimento"]).toBe("entrada");

    vi.advanceTimersByTime(INTRO_MS - 3_000);

    expect(host.dataset["movimento"]).toBe("parado");
    expect(host.dataset["giro"]).toBe(REST_YAW_DEGREES);
  });

  it("arrastar no meio interrompe a entrada onde ela está, e o prazo não a reposiciona depois", () => {
    const start = performance.now();
    stage.playIntro();
    loop.frame(start);
    const startYaw = host.dataset["giro"];
    stage.stopIntro();
    expect(host.dataset["movimento"]).toBe("parado");

    vi.advanceTimersByTime(INTRO_MS);

    expect(host.dataset["giro"]).toBe(startYaw);
    expect(host.dataset["movimento"]).toBe("parado");
  });
});
