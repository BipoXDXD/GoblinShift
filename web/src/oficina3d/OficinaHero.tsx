import { useEffect, useRef, useState, type ReactElement } from "react";

import posterAvif192 from "../assets/art/oficina-3d/oficina-3d-rubia-192w.avif";
import posterWebp192 from "../assets/art/oficina-3d/oficina-3d-rubia-192w.webp";
import posterAvif384 from "../assets/art/oficina-3d/oficina-3d-rubia-384w.avif";
import posterWebp384 from "../assets/art/oficina-3d/oficina-3d-rubia-384w.webp";
import posterAvif640 from "../assets/art/oficina-3d/oficina-3d-rubia-640w.avif";
import posterWebp640 from "../assets/art/oficina-3d/oficina-3d-rubia-640w.webp";
import { TEXT } from "../text";
import { browserEnvironment, shouldLoad3D, type Environment3D } from "./conditions";

export interface MountOptions {
  /** Abortado quando a página sai antes de a cena ficar pronta. */
  readonly signal: AbortSignal;
  /** O navegador tirou o contexto WebGL (GPU reiniciada, memória): volta a imagem. */
  readonly onContextLost: () => void;
}

export interface SceneHandle {
  readonly dispose: () => void;
}

/** O chunk `oficina-3d`, carregado por `import()` só quando a decisão é carregar. */
export interface SceneModule {
  readonly mountScene: (host: HTMLElement, options: MountOptions) => Promise<SceneHandle>;
}

/** `imagem` antes e no lugar da cena; `falhou` também mostra a imagem, que é o fallback. */
type HeroState = "imagem" | "carregando" | "pronto" | "falhou";

interface OficinaHeroProps {
  readonly environment?: Environment3D;
  /** Agenda a carga depois do conteúdo; devolve o cancelamento. */
  readonly whenPageIdle?: (run: () => void) => () => void;
  readonly loadScene?: () => Promise<SceneModule>;
}

const POSTER_WIDTH_PX = 640;
const POSTER_HEIGHT_PX = 800;
const POSTER_SIZES = "(min-width: 768px) 320px, 192px";
/** Teto da espera por um momento ocioso; passado isso, a carga começa de qualquer jeito. */
const IDLE_TIMEOUT_MS = 2_000;

/**
 * Cena 3D da abertura (ADR 027 e 030): a imagem estática aparece com a página e é o fallback; a
 * cena chega depois do `load` e de um momento ocioso, num espaço decorativo (`aria-hidden`), e
 * nunca carrega com movimento reduzido, economia de dados ou sem WebGL 2.
 */
export function OficinaHero({
  environment = browserEnvironment,
  whenPageIdle = afterLoadAndIdle,
  loadScene = loadOficina3dChunk,
}: OficinaHeroProps): ReactElement {
  const hostRef = useRef<HTMLDivElement>(null);
  const [state, setState] = useState<HeroState>("imagem");

  useEffect(() => {
    const host = hostRef.current;
    if (host === null) {
      return undefined;
    }
    const controller = new AbortController();
    let handle: SceneHandle | null = null;
    const start = async (): Promise<void> => {
      if (!shouldLoad3D(environment)) {
        return;
      }
      setState("carregando");
      const scene = await loadScene();
      const mounted = await scene.mountScene(host, {
        signal: controller.signal,
        onContextLost: () => {
          setState("falhou");
        },
      });
      if (controller.signal.aborted) {
        mounted.dispose();
        return;
      }
      handle = mounted;
      setState("pronto");
    };
    const cancelSchedule = whenPageIdle(() => {
      // A cena é decorativa: qualquer falha (chunk, GLB, WebGL) deixa a imagem no lugar e marca o
      // estado, que o E2E confere. Nada essencial depende dela.
      start().catch(() => {
        if (!controller.signal.aborted) {
          setState("falhou");
        }
      });
    });
    return () => {
      cancelSchedule();
      controller.abort();
      handle?.dispose();
    };
  }, [environment, whenPageIdle, loadScene]);

  const sceneShown = state === "pronto";
  return (
    <div
      data-oficina-3d={state}
      className="relative mx-auto aspect-4/5 w-full max-w-48 shrink-0 md:mx-0 md:max-w-xs"
    >
      <picture>
        <source
          type="image/avif"
          srcSet={`${posterAvif192} 192w, ${posterAvif384} 384w, ${posterAvif640} 640w`}
          sizes={POSTER_SIZES}
        />
        <source
          type="image/webp"
          srcSet={`${posterWebp192} 192w, ${posterWebp384} 384w, ${posterWebp640} 640w`}
          sizes={POSTER_SIZES}
        />
        <img
          src={posterWebp640}
          alt={TEXT.art.hero}
          width={POSTER_WIDTH_PX}
          height={POSTER_HEIGHT_PX}
          fetchPriority="high"
          decoding="async"
          className={`size-full object-contain motion-safe:transition-opacity ${sceneShown ? "opacity-0" : "opacity-100"}`}
        />
      </picture>
      <div
        ref={hostRef}
        aria-hidden="true"
        className={`absolute inset-0 touch-pan-y motion-safe:transition-opacity ${sceneShown ? "opacity-100" : "opacity-0"}`}
      />
    </div>
  );
}

function loadOficina3dChunk(): Promise<SceneModule> {
  return import("./oficina-3d");
}

/** Espera o `load` da página e depois um momento ocioso (ou o teto), para não disputar o LCP. */
function afterLoadAndIdle(run: () => void): () => void {
  let cancelIdle = (): void => undefined;
  const onLoad = (): void => {
    // O Safari ainda não tem requestIdleCallback em todas as versões suportadas.
    if (typeof window.requestIdleCallback === "function") {
      const id = window.requestIdleCallback(run, { timeout: IDLE_TIMEOUT_MS });
      cancelIdle = () => {
        window.cancelIdleCallback(id);
      };
    } else {
      const id = window.setTimeout(run, IDLE_TIMEOUT_MS);
      cancelIdle = () => {
        window.clearTimeout(id);
      };
    }
  };
  if (document.readyState === "complete") {
    onLoad();
  } else {
    window.addEventListener("load", onLoad, { once: true });
  }
  return () => {
    window.removeEventListener("load", onLoad);
    cancelIdle();
  };
}
