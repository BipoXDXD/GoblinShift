import { describe, expect, it } from "vitest";

import {
  MAX_PITCH_DEGREES,
  MIN_PITCH_DEGREES,
  REST_POSE,
  dragTurn,
  keyTurn,
  turned,
  twoFingerTilt,
  type Pose,
} from "./pose";

const DEGREE = Math.PI / 180;

function degrees(pose: Pose): { yaw: number; pitch: number } {
  return { yaw: Math.round(pose.yaw / DEGREE), pitch: Math.round(pose.pitch / DEGREE) };
}

describe("turned", () => {
  it("soma o giro e a inclinação", () => {
    const pose = turned({ yaw: 0, pitch: 0 }, { yaw: 10 * DEGREE, pitch: 5 * DEGREE });

    expect(degrees(pose)).toEqual({ yaw: 10, pitch: 5 });
  });

  it.each([
    [34, 1, 35],
    [35, 1, 35],
    [0, 90, 35],
  ])("de %d° inclinando %d° para cima para no teto de 35°", (from, by, expected) => {
    const pose = turned({ yaw: 0, pitch: from * DEGREE }, { yaw: 0, pitch: by * DEGREE });

    expect(degrees(pose).pitch).toBe(expected);
  });

  it.each([
    [-14, -1, -15],
    [-15, -1, -15],
    [0, -90, -15],
  ])("de %d° inclinando %d° para baixo para no piso de -15°", (from, by, expected) => {
    const pose = turned({ yaw: 0, pitch: from * DEGREE }, { yaw: 0, pitch: by * DEGREE });

    expect(degrees(pose).pitch).toBe(expected);
  });

  it("o teto e o piso são os do desenho: ver de cima e de baixo sem atravessar o chão", () => {
    expect([MIN_PITCH_DEGREES, MAX_PITCH_DEGREES]).toEqual([-15, 35]);
  });

  it.each([
    [170, 20, -170],
    [-170, -20, 170],
    [0, 360, 0],
    [0, 720 + 90, 90],
  ])("o giro é livre: de %d° girando %d° chega a %d°", (from, by, expected) => {
    const pose = turned({ yaw: from * DEGREE, pitch: 0 }, { yaw: by * DEGREE, pitch: 0 });

    expect(degrees(pose).yaw).toBe(expected);
  });
});

describe("keyTurn", () => {
  it.each<[string, { yaw: number; pitch: number }]>([
    ["ArrowRight", { yaw: 15, pitch: 0 }],
    ["ArrowLeft", { yaw: -15, pitch: 0 }],
    ["ArrowUp", { yaw: 0, pitch: 10 }],
    ["ArrowDown", { yaw: 0, pitch: -10 }],
  ])("%s gira ou inclina um passo", (key, expected) => {
    const turn = keyTurn(key);

    expect(turn && degrees(turn)).toEqual(expected);
  });

  it.each(["Enter", " ", "Tab", "a", "PageDown"])("%j não é comando de giro", (key) => {
    expect(keyTurn(key)).toBeNull();
  });
});

describe("dragTurn", () => {
  it("com o mouse, o arrasto horizontal gira e o vertical inclina", () => {
    const turn = dragTurn("mouse", { dx: 100, dy: 50 });

    expect(degrees(turn).yaw).toBeGreaterThan(0);
    expect(degrees(turn).pitch).toBeGreaterThan(0);
  });

  it("com a caneta, também inclina", () => {
    expect(dragTurn("pen", { dx: 0, dy: 50 }).pitch).toBeGreaterThan(0);
  });

  it("com um dedo, só gira: o arrasto vertical continua rolando a página", () => {
    const turn = dragTurn("touch", { dx: 100, dy: 50 });

    expect(turn.yaw).toBeGreaterThan(0);
    expect(turn.pitch).toBe(0);
  });

  it("o mesmo deslocamento gira o mesmo ângulo no mouse e no dedo", () => {
    expect(dragTurn("touch", { dx: 37, dy: 0 }).yaw).toBe(dragTurn("mouse", { dx: 37, dy: 0 }).yaw);
  });
});

describe("REST_POSE", () => {
  it("é a pose da imagem estática: render a 20° e câmera na altura do centro", () => {
    expect(degrees(REST_POSE)).toEqual({ yaw: -20, pitch: 0 });
  });
});

describe("twoFingerTilt", () => {
  it("dois dedos descendo inclinam para ver de cima, sem girar", () => {
    const turn = twoFingerTilt(50);

    expect(turn.yaw).toBe(0);
    expect(turn.pitch).toBeGreaterThan(0);
  });

  it("inclina no mesmo ritmo do mouse", () => {
    expect(twoFingerTilt(37).pitch).toBe(dragTurn("mouse", { dx: 0, dy: 37 }).pitch);
  });
});
