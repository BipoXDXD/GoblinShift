// Arrasto no palco 3D (ADR 038): o mouse e a caneta giram e inclinam; um dedo só gira, porque o
// arrasto vertical com um dedo é a rolagem da página (`touch-action: pan-y` no palco); dois dedos
// subindo ou descendo juntos inclinam. Sem three: só traduz ponteiros em giros da pose.
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
  // Com dois dedos, o gesto é da cena: a página não rola nem dá zoom. Com um, nada muda aqui.
  const onTouchMove = (event: TouchEvent): void => {
    if (event.touches.length >= TWO_FINGERS && event.cancelable) {
      event.preventDefault();
    }
  };

  host.addEventListener("pointerdown", onDown);
  host.addEventListener("pointermove", onMove);
  host.addEventListener("pointerup", onEnd);
  host.addEventListener("pointercancel", onEnd);
  host.addEventListener("touchmove", onTouchMove, { passive: false });
  return () => {
    host.removeEventListener("pointerdown", onDown);
    host.removeEventListener("pointermove", onMove);
    host.removeEventListener("pointerup", onEnd);
    host.removeEventListener("pointercancel", onEnd);
    host.removeEventListener("touchmove", onTouchMove);
  };
}

function touchesDown(pressed: ReadonlyMap<number, Point>): number {
  return [...pressed.values()].filter((point) => point.type === "touch").length;
}
