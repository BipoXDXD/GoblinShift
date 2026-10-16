import { RotateCcw, RotateCw } from "lucide-react";
import { useEffect, useId, useRef, useState, type KeyboardEvent, type ReactElement } from "react";

import { Button } from "../components/ui/Button";
import { TEXT } from "../text";
import { CAST, FIRST_ON_STAGE, castMember, type CastId, type CastMember } from "./cast";
import { browserEnvironment, shouldLoad3D, type Environment3D } from "./conditions";
import { keyTurn, type Pose } from "./pose";

const T = TEXT.art.stage;

export interface MountOptions {
  /** O navegador tirou o contexto WebGL (GPU reiniciada, memória): volta a imagem. */
  readonly onContextLost: () => void;
}

/** A cena montada: um renderer para o elenco todo, que troca de modelo sob demanda (ADR 038). */
export interface SceneHandle {
  /** Baixa o GLB da goblin e a põe no palco; com o sinal abortado, a carga é descartada. */
  readonly show: (goblin: CastId, signal: AbortSignal) => Promise<void>;
  readonly turn: (by: Pose) => void;
  readonly resetPose: () => void;
  readonly setSpinning: (spinning: boolean) => void;
  readonly dispose: () => void;
}

/** O chunk `oficina-3d`, carregado por `import()` só quando a decisão é carregar. */
export interface SceneModule {
  readonly mountScene: (host: HTMLElement, options: MountOptions) => SceneHandle;
}

/** `imagem` antes e no lugar da cena; `falhou` também mostra a imagem, que é o fallback. */
type HeroState = "imagem" | "carregando" | "pronto" | "falhou";
type MountState = "imagem" | "carregando" | "montado" | "falhou";

/** O resultado da última carga de modelo, de qual goblin. */
interface ModelResult {
  readonly goblin: CastId;
  readonly status: "pronto" | "falhou";
}

interface OficinaHeroProps {
  readonly environment?: Environment3D;
  /** Agenda a carga depois do conteúdo; devolve o cancelamento. */
  readonly whenPageIdle?: (run: () => void) => () => void;
  readonly loadScene?: () => Promise<SceneModule>;
}

const POSTER_WIDTH_PX = 640;
const POSTER_HEIGHT_PX = 800;
const POSTER_SIZES = "(min-width: 768px) 320px, 192px";
const THUMBNAIL_WIDTH_PX = 192;
const THUMBNAIL_HEIGHT_PX = 240;
/** Teto da espera por um momento ocioso; passado isso, a carga começa de qualquer jeito. */
const IDLE_TIMEOUT_MS = 2_000;
const RESET_KEY = "Home";

/**
 * Palco 3D da abertura (ADR 027, 030 e 038): uma goblin por vez, escolhida numa fileira de cinco
 * botões. A imagem estática da goblin escolhida aparece com a página e é o fallback; a cena chega
 * depois do `load` e de um momento ocioso, e nunca carrega com movimento reduzido, economia de
 * dados ou sem WebGL 2. Trocar de goblin baixa só o GLB dela, com a imagem dela enquanto carrega.
 */
export function OficinaHero({
  environment = browserEnvironment,
  whenPageIdle = afterLoadAndIdle,
  loadScene = loadOficina3dChunk,
}: OficinaHeroProps): ReactElement {
  const hostRef = useRef<HTMLDivElement>(null);
  const hintId = useId();
  const [mount, setMount] = useState<MountState>("imagem");
  const [handle, setHandle] = useState<SceneHandle | null>(null);
  const [selected, setSelected] = useState<CastId>(FIRST_ON_STAGE);
  const [model, setModel] = useState<ModelResult | null>(null);
  const [spinning, setSpinning] = useState(false);

  useEffect(() => {
    const host = hostRef.current;
    if (host === null) {
      return undefined;
    }
    let cancelled = false;
    let mounted: SceneHandle | null = null;
    const start = async (): Promise<void> => {
      if (!shouldLoad3D(environment)) {
        return;
      }
      setMount("carregando");
      const scene = await loadScene();
      if (cancelled) {
        return;
      }
      mounted = scene.mountScene(host, {
        onContextLost: () => {
          setHandle(null);
          setMount("falhou");
        },
      });
      setHandle(mounted);
      setMount("montado");
    };
    const cancelSchedule = whenPageIdle(() => {
      // A cena é decorativa: qualquer falha (chunk, WebGL) deixa a imagem no lugar e marca o
      // estado, que o E2E confere. Nada essencial depende dela.
      start().catch(() => {
        if (!cancelled) {
          setMount("falhou");
        }
      });
    });
    return () => {
      cancelled = true;
      cancelSchedule();
      mounted?.dispose();
    };
  }, [environment, whenPageIdle, loadScene]);

  useEffect(() => {
    if (handle === null) {
      return undefined;
    }
    const controller = new AbortController();
    const settle = (status: ModelResult["status"]) => (): void => {
      if (!controller.signal.aborted) {
        setModel({ goblin: selected, status });
      }
    };
    handle.show(selected, controller.signal).then(settle("pronto"), settle("falhou"));
    return () => {
      controller.abort();
    };
  }, [handle, selected]);

  useEffect(() => {
    handle?.setSpinning(spinning);
  }, [handle, spinning]);

  const state = heroStateOf(mount, model, selected);
  const member = castMember(selected);
  const sceneShown = state === "pronto";
  // O giro automático é movimento que a pessoa liga; com movimento reduzido nem a cena carrega,
  // e a checagem aqui cobre a preferência ligada depois da carga.
  const canSpin = handle !== null && !environment.prefersReducedMotion();

  const onStageKey = (event: KeyboardEvent<HTMLDivElement>): void => {
    if (handle === null) {
      return;
    }
    if (event.key === RESET_KEY) {
      event.preventDefault();
      setSpinning(false);
      handle.resetPose();
      return;
    }
    const turn = keyTurn(event.key);
    if (turn !== null) {
      event.preventDefault();
      handle.turn(turn);
    }
  };

  return (
    <div className="mx-auto flex w-full max-w-xs shrink-0 flex-col items-center gap-4 md:mx-0">
      <div
        data-oficina-3d={state}
        data-goblin={selected}
        {...(sceneShown
          ? {
              role: "application",
              tabIndex: 0,
              "aria-label": T.label(member.name),
              "aria-describedby": hintId,
              onKeyDown: onStageKey,
            }
          : {})}
        className="relative aspect-4/5 w-full max-w-48 rounded-lg md:max-w-xs"
      >
        <picture>
          <source type="image/avif" srcSet={member.avif} sizes={POSTER_SIZES} />
          <source type="image/webp" srcSet={member.webp} sizes={POSTER_SIZES} />
          <img
            src={member.fallback}
            alt={member.alt}
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
          className={`absolute inset-0 cursor-grab touch-pan-y active:cursor-grabbing motion-safe:transition-opacity ${sceneShown ? "opacity-100" : "opacity-0"}`}
        />
      </div>
      {handle === null ? null : (
        <>
          <div className="flex flex-wrap justify-center gap-2">
            {canSpin ? (
              <Button
                type="button"
                variant="secondary"
                aria-pressed={spinning}
                onClick={() => {
                  setSpinning(!spinning);
                }}
              >
                <RotateCw aria-hidden="true" className="size-4" />
                {T.spin}
              </Button>
            ) : null}
            <Button
              type="button"
              variant="secondary"
              onClick={() => {
                setSpinning(false);
                handle.resetPose();
              }}
            >
              <RotateCcw aria-hidden="true" className="size-4" />
              {T.reset}
            </Button>
          </div>
          <p id={hintId} className="text-center text-sm text-fg-muted">
            {T.hint}
          </p>
        </>
      )}
      <div role="group" aria-label={T.castLabel} className="grid w-full grid-cols-3 gap-2">
        {CAST.map((goblin) => (
          <CastButton
            key={goblin.id}
            goblin={goblin}
            pressed={goblin.id === selected}
            onChoose={() => {
              setSelected(goblin.id);
            }}
          />
        ))}
      </div>
    </div>
  );
}

function heroStateOf(mount: MountState, model: ModelResult | null, selected: CastId): HeroState {
  if (mount !== "montado") {
    return mount;
  }
  return model?.goblin === selected ? model.status : "carregando";
}

interface CastButtonProps {
  readonly goblin: CastMember;
  readonly pressed: boolean;
  readonly onChoose: () => void;
}

/** Botão do seletor: miniatura e nome; o escolhido ganha superfície, sombra e borda, além da cor. */
function CastButton({ goblin, pressed, onChoose }: CastButtonProps): ReactElement {
  return (
    <button
      type="button"
      aria-pressed={pressed}
      onClick={onChoose}
      className="flex min-h-11 cursor-pointer flex-col items-center gap-1 rounded-lg border-2 border-transparent p-1 text-sm font-semibold text-fg-muted transition hover:bg-surface hover:text-fg aria-pressed:border-primary aria-pressed:bg-surface aria-pressed:text-fg aria-pressed:shadow-card"
    >
      {/* A imagem é de corpo inteiro; o círculo mostra o rosto, ampliado a partir do topo. */}
      <picture className="block size-12 overflow-hidden rounded-full bg-surface-sunken">
        <source type="image/avif" srcSet={goblin.thumbnail.avif} />
        <img
          src={goblin.thumbnail.webp}
          alt=""
          width={THUMBNAIL_WIDTH_PX}
          height={THUMBNAIL_HEIGHT_PX}
          fetchPriority="low"
          decoding="async"
          className="size-full origin-top scale-200 object-cover object-top"
        />
      </picture>
      {goblin.name}
    </button>
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
