import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { attachGestures, type GestureHandlers } from "./gestures";
import type { Pose } from "./pose";

let host: HTMLDivElement;
let turns: Pose[];
let handlers: GestureHandlers;
let detach: () => void;
let capture: ReturnType<typeof vi.fn<(pointerId: number) => void>>;

function pointer(type: string, pointerId: number, x: number, y: number, pointerType = "mouse") {
  host.dispatchEvent(
    new PointerEvent(type, { pointerId, clientX: x, clientY: y, pointerType, bubbles: true }),
  );
}

function touchMove(fingers: number): TouchEvent {
  const touches: readonly object[] = Array.from({ length: fingers }, () => ({}));
  const event = new TouchEvent("touchmove", { cancelable: true, bubbles: true });
  Object.defineProperty(event, "touches", { value: touches });
  host.dispatchEvent(event);
  return event;
}

beforeEach(() => {
  host = document.createElement("div");
  capture = vi.fn<(pointerId: number) => void>();
  host.setPointerCapture = capture;
  document.body.append(host);
  turns = [];
  handlers = {
    onStart: vi.fn(),
    onTurn: (by) => {
      turns.push(by);
    },
  };
  detach = attachGestures(host, handlers);
});

afterEach(() => {
  detach();
  host.remove();
});

describe("attachGestures", () => {
  it("arrastar com o mouse gira e inclina, e avisa o começo", () => {
    pointer("pointerdown", 1, 100, 100);
    pointer("pointermove", 1, 150, 120);

    expect(handlers.onStart).toHaveBeenCalledOnce();
    expect(capture).toHaveBeenCalledWith(1);
    expect(turns).toHaveLength(1);
    expect(turns[0]?.yaw).toBeCloseTo(0.5);
    expect(turns[0]?.pitch).toBeCloseTo(0.2);
  });

  it("mover sem apertar não gira", () => {
    pointer("pointermove", 1, 150, 120);

    expect(turns).toEqual([]);
  });

  it.each(["pointerup", "pointercancel"])("depois de %s o movimento não gira mais", (end) => {
    pointer("pointerdown", 1, 100, 100);
    pointer(end, 1, 100, 100);
    pointer("pointermove", 1, 150, 120);

    expect(turns).toEqual([]);
  });

  it("um dedo só gira", () => {
    pointer("pointerdown", 1, 100, 100, "touch");
    pointer("pointermove", 1, 150, 160, "touch");

    expect(turns[0]?.yaw).toBeCloseTo(0.5);
    expect(turns[0]?.pitch).toBe(0);
  });

  it("dois dedos subindo juntos inclinam pelo ponto médio, sem girar", () => {
    pointer("pointerdown", 1, 100, 100, "touch");
    pointer("pointerdown", 2, 200, 100, "touch");
    pointer("pointermove", 1, 130, 140, "touch");
    pointer("pointermove", 2, 230, 140, "touch");

    expect(turns.map((turn) => turn.yaw)).toEqual([0, 0]);
    // Cada dedo desceu 40 px; o ponto médio, 20 px por movimento.
    expect(turns[0]?.pitch).toBeCloseTo(0.2);
    expect(turns[1]?.pitch).toBeCloseTo(0.2);
  });

  it("com dois dedos, a página não rola; com um, rola", () => {
    expect(touchMove(2).defaultPrevented).toBe(true);
    expect(touchMove(1).defaultPrevented).toBe(false);
  });

  it("ao soltar, para de ouvir", () => {
    detach();
    pointer("pointerdown", 1, 100, 100);
    pointer("pointermove", 1, 150, 120);

    expect(turns).toEqual([]);
    expect(touchMove(2).defaultPrevented).toBe(false);
  });
});
