// Ângulo de vista da goblin no palco 3D (ADR 038): giro livre na horizontal e inclinação limitada
// na vertical. Puro, sem three nem DOM: a cena aplica a pose, e o herói e o arrasto a calculam.

/** Ângulos em radianos. `yaw` gira a goblin; `pitch` sobe a câmera (positivo vê de cima). */
export interface Pose {
  readonly yaw: number;
  readonly pitch: number;
}

const DEGREE = Math.PI / 180;
const FULL_TURN = 2 * Math.PI;

/** Abaixo de -15° a câmera passaria do plano dos pés; acima de 35° o rosto some sob o cabelo. */
export const MIN_PITCH_DEGREES = -15;
export const MAX_PITCH_DEGREES = 35;

/** Mesmo ângulo da imagem estática (render a 20°), para a troca não dar salto. */
export const REST_POSE: Pose = { yaw: -0.35, pitch: 0 };

/** Um toque de seta: passos que a pessoa conta (24 para a volta inteira, 5 de piso a teto). */
const KEY_YAW = 15 * DEGREE;
const KEY_PITCH = 10 * DEGREE;
/** Arrastar 1 px gira isto, na horizontal e na vertical. */
const DRAG_RADIANS_PER_PX = 0.01;

const KEY_TURNS: Readonly<Record<string, Pose>> = {
  ArrowRight: { yaw: KEY_YAW, pitch: 0 },
  ArrowLeft: { yaw: -KEY_YAW, pitch: 0 },
  ArrowUp: { yaw: 0, pitch: KEY_PITCH },
  ArrowDown: { yaw: 0, pitch: -KEY_PITCH },
};

/** A pose depois de girar e inclinar: o giro dá a volta, a inclinação para no piso e no teto. */
export function turned(pose: Pose, by: Pose): Pose {
  return {
    yaw: wrapAngle(pose.yaw + by.yaw),
    pitch: Math.min(
      MAX_PITCH_DEGREES * DEGREE,
      Math.max(MIN_PITCH_DEGREES * DEGREE, pose.pitch + by.pitch),
    ),
  };
}

/** O passo de uma seta do teclado, ou `null` se a tecla não gira nem inclina. */
export function keyTurn(key: string): Pose | null {
  return Object.hasOwn(KEY_TURNS, key) ? (KEY_TURNS[key] ?? null) : null;
}

/**
 * O giro de um arrasto de um ponteiro. Com um dedo, só a horizontal conta: o arrasto vertical é
 * da página (`touch-action: pan-y pinch-zoom`). Botões também inclinam sem arrastar.
 */
export function dragTurn(pointerType: string, moved: { dx: number; dy: number }): Pose {
  return {
    yaw: moved.dx * DRAG_RADIANS_PER_PX,
    pitch: pointerType === "touch" ? 0 : moved.dy * DRAG_RADIANS_PER_PX,
  };
}

/** Dois dedos subindo ou descendo juntos inclinam; `dy` é o deslocamento do ponto médio. */
export function twoFingerTilt(dy: number): Pose {
  return { yaw: 0, pitch: dy * DRAG_RADIANS_PER_PX };
}

/** Leva o ângulo para (-π, π], para o giro livre não acumular voltas sem fim. */
function wrapAngle(radians: number): number {
  const wrapped = radians - FULL_TURN * Math.round(radians / FULL_TURN);
  return wrapped === -Math.PI ? Math.PI : wrapped;
}
