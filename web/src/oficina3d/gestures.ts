// Arrasto no palco 3D (ADR 038): o mouse e a caneta giram e inclinam; um dedo só gira, porque o
// arrasto vertical com um dedo é a rolagem da página (`touch-action: pan-y pinch-zoom` no palco); dois dedos
// subindo ou descendo juntos inclinam enquanto o navegador não assumir pan/zoom.
// A navegação nativa tem prioridade; pointercancel/lostpointercapture encerram o arrasto.
import { dragTurn, twoFingerTilt, type Pose } from "./pose";

export interface GestureHandlers {
  /** A pessoa encostou no palco: a entrada automática para. */
  readonly onStart: () => void;
  readonly onTurn: (by: Pose) => void;
}

interface Point {
  readonly x: number;
  readonly y: number;
  readonly type: string;
}

const TWO_FINGERS = 2;

/** Liga os gestos ao palco; devolve o desligamento. */
export function attachGestures(host: HTMLElement, handlers: GestureHandlers): () => void {
  const pressed = new Map<number, Point>();

  const onDown = (event: PointerEvent): void => {
    handlers.onStart();
    pressed.set(event.pointerId, { x: event.clientX, y: event.clientY, type: event.pointerType });
    host.setPointerCapture(event.pointerId);
  };
  const onMove = (event: PointerEvent): void => {
    const last = pressed.get(event.pointerId);
    if (last === undefined) {
      return;
    }
    pressed.set(event.pointerId, { ...last, x: event.clientX, y: event.clientY });
    const moved = { dx: event.clientX - last.x, dy: event.clientY - last.y };
    handlers.onTurn(
      touchesDown(pressed) >= TWO_FINGERS
        ? // Um dedo andou; o ponto médio dos dois anda a metade.
          twoFingerTilt(moved.dy / TWO_FINGERS)
        : dragTurn(last.type, moved),
    );
  };
  const onEnd = (event: PointerEvent): void => {
    pressed.delete(event.pointerId);
  };
  host.addEventListener("pointerdown", onDown);
  host.addEventListener("pointermove", onMove);
  host.addEventListener("pointerup", onEnd);
  host.addEventListener("pointercancel", onEnd);
  host.addEventListener("lostpointercapture", onEnd);
  return () => {
    host.removeEventListener("pointerdown", onDown);
    host.removeEventListener("pointermove", onMove);
    host.removeEventListener("pointerup", onEnd);
    host.removeEventListener("pointercancel", onEnd);
    host.removeEventListener("lostpointercapture", onEnd);
    pressed.clear();
  };
}

function touchesDown(pressed: ReadonlyMap<number, Point>): number {
  return [...pressed.values()].filter((point) => point.type === "touch").length;
}
