// A pose da goblin no palco 3D e o laço que a anima (ADR 038). Sem three: recebe só o laço de
// animação do renderer e a vista que aplica a pose e desenha, para o Vitest exercitar o relógio.
import { REST_POSE, turned, type Pose } from "./pose";

/** Entrada: gira de um lado para o outro e para; abaixo dos 5 s da WCAG 2.2.2, sem botão. */
const INTRO_MS = 3_500;
const INTRO_FROM_RADIANS = 0.9;
/** Auto-rotação do botão Girar: uma volta a cada 12 s. */
const SPIN_RADIANS_PER_MS = (2 * Math.PI) / 12_000;

/** O laço de quadros do renderer (o `setAnimationLoop` do WebGLRenderer). */
export interface AnimationLoop {
  readonly setAnimationLoop: (callback: ((time: number) => void) | null) => void;
}

export interface StageView {
  readonly applyPose: (pose: Pose) => void;
  readonly render: () => void;
}

type Motion = "entrada" | "girando" | "parado";

/**
 * A pose da goblin e o que a mexe: a entrada (uma vez, até 3,5 s, nunca com movimento reduzido), a
 * auto-rotação do botão Girar, o arrasto e as setas. O laço de animação só roda com entrada ou
 * giro; fora disso o canvas redesenha só quando a pose muda, sem gastar bateria. Marca `data-movimento`, `data-giro` e
 * `data-inclinacao` (graus) no host para o E2E.
 *
 * A entrada acaba por prazo, não por quadro: o relógio começa no `playIntro` e um timer a encerra
 * em INTRO_MS. Esperar o quadro seguinte ao prazo fazia a duração depender da taxa de quadros: no
 * SwiftShader do CI, com um quadro a cada ~5 s, a entrada passava dos 5 s da WCAG 2.2.2.
 */
export class Stage {
  readonly #host: HTMLElement;
  readonly #renderer: AnimationLoop;
  readonly #view: StageView;
  #pose: Pose = REST_POSE;
  #introStart = 0;
  #introRunning = false;
  #introDeadline: ReturnType<typeof setTimeout> | null = null;
  #spinning = false;
  #reducedMotion = false;
  #lastFrame: number | null = null;

  constructor(host: HTMLElement, renderer: AnimationLoop, view: StageView) {
    this.#host = host;
    this.#renderer = renderer;
    this.#view = view;
    this.#mark();
  }

  render(): void {
    this.#view.render();
  }

  playIntro(): void {
    if (this.#reducedMotion) {
      // Sem a entrada, a goblin chega já na pose de repouso.
      this.#pose = REST_POSE;
      this.#apply();
      this.#loop();
      return;
    }
    this.#pose = { yaw: INTRO_FROM_RADIANS, pitch: REST_POSE.pitch };
    this.#introStart = performance.now();
    this.#introRunning = true;
    this.#clearIntroDeadline();
    this.#introDeadline = setTimeout(() => {
      this.#introDeadline = null;
      this.#finishIntro();
    }, INTRO_MS);
    this.#apply();
    this.#loop();
  }

  stopIntro(): void {
    this.#introRunning = false;
    this.#loop();
  }

  turn(by: Pose): void {
    this.#pose = turned(this.#pose, by);
    this.#apply();
  }

  resetPose(): void {
    this.#introRunning = false;
    this.#spinning = false;
    this.#pose = REST_POSE;
    this.#apply();
    this.#loop();
  }

  setSpinning(spinning: boolean): void {
    this.#spinning = spinning;
    if (spinning) {
      this.#introRunning = false;
    }
    this.#loop();
  }

  setReducedMotion(reduced: boolean): void {
    this.#reducedMotion = reduced;
    if (reduced) {
      this.#finishIntro();
    }
  }

  stop(): void {
    this.#introRunning = false;
    this.#spinning = false;
    this.#clearIntroDeadline();
    this.#renderer.setAnimationLoop(null);
  }

  /** Liga o laço se há o que animar e desliga quando não há. */
  #loop(): void {
    this.#mark();
    if (!this.#introRunning) {
      this.#clearIntroDeadline();
    }
    if (!this.#introRunning && !this.#spinning) {
      this.#renderer.setAnimationLoop(null);
      this.#lastFrame = null;
      return;
    }
    this.#renderer.setAnimationLoop((now) => {
      this.#frame(now);
    });
  }

  #frame(now: number): void {
    const elapsed = this.#lastFrame === null ? 0 : now - this.#lastFrame;
    this.#lastFrame = now;
    if (this.#introRunning) {
      // O carimbo do quadro pode ser um pouco anterior ao playIntro: começa do zero.
      const progress = Math.min(1, Math.max(0, now - this.#introStart) / INTRO_MS);
      const eased = 1 - (1 - progress) ** 3;
      this.#pose = {
        ...this.#pose,
        yaw: INTRO_FROM_RADIANS + (REST_POSE.yaw - INTRO_FROM_RADIANS) * eased,
      };
      if (progress === 1) {
        this.#introRunning = false;
      }
    } else if (this.#spinning) {
      this.#pose = turned(this.#pose, { yaw: elapsed * SPIN_RADIANS_PER_MS, pitch: 0 });
    }
    this.#apply();
    if (!this.#introRunning && !this.#spinning) {
      this.#loop();
    }
  }

  /** A entrada interrompida (prazo ou movimento reduzido) termina onde terminaria, sem animar o resto. */
  #finishIntro(): void {
    if (!this.#introRunning) {
      return;
    }
    this.#introRunning = false;
    this.#pose = { ...this.#pose, yaw: REST_POSE.yaw };
    this.#apply();
    this.#loop();
  }

  #clearIntroDeadline(): void {
    if (this.#introDeadline !== null) {
      clearTimeout(this.#introDeadline);
      this.#introDeadline = null;
    }
  }

  #apply(): void {
    this.#view.applyPose(this.#pose);
    this.#view.render();
    this.#host.dataset["giro"] = String(Math.round((this.#pose.yaw * 180) / Math.PI));
    this.#host.dataset["inclinacao"] = String(Math.round((this.#pose.pitch * 180) / Math.PI));
  }

  #mark(): void {
    const motion: Motion = this.#introRunning ? "entrada" : this.#spinning ? "girando" : "parado";
    this.#host.dataset["movimento"] = motion;
  }
}
