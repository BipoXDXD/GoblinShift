import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { TEXT } from "../text";
import type { CastId } from "./cast";
import type { Environment3D } from "./conditions";
import { OficinaHero, type MountOptions, type SceneHandle, type SceneModule } from "./OficinaHero";
import { keyTurn } from "./pose";

const READY: Environment3D = {
  prefersReducedMotion: () => false,
  subscribeReducedMotion: () => () => undefined,
  savesData: () => false,
  supportsWebGL2: () => true,
};

const REDUCED_MOTION: Environment3D = { ...READY, prefersReducedMotion: () => true };

/** Ambiente pronto em que a pessoa liga o movimento reduzido no meio do caminho. */
function reducibleMotion(): { readonly environment: Environment3D; readonly reduce: () => void } {
  let reduced = false;
  const listeners = new Set<() => void>();
  return {
    environment: {
      ...READY,
      prefersReducedMotion: () => reduced,
      subscribeReducedMotion: (onChange) => {
        listeners.add(onChange);
        return () => {
          listeners.delete(onChange);
        };
      },
    },
    reduce: () => {
      reduced = true;
      listeners.forEach((onChange) => {
        onChange();
      });
    },
  };
}

function runNow(run: () => void): () => void {
  run();
  return () => undefined;
}

function never(): () => void {
  return () => undefined;
}

interface Deferred {
  readonly resolve: () => void;
  readonly reject: (error: Error) => void;
}

interface ShowCall {
  readonly goblin: CastId;
  readonly signal: AbortSignal;
}

interface FakeScene {
  readonly loadScene: () => Promise<SceneModule>;
  readonly host: () => HTMLElement | undefined;
  readonly options: () => MountOptions | undefined;
  readonly shows: ShowCall[];
  /** Cargas pendentes, na ordem dos `show`. */
  readonly pending: Deferred[];
  readonly handle: {
    readonly turn: ReturnType<typeof vi.fn>;
    readonly resetPose: ReturnType<typeof vi.fn>;
    readonly setSpinning: ReturnType<typeof vi.fn>;
    readonly setReducedMotion: ReturnType<typeof vi.fn>;
    readonly dispose: ReturnType<typeof vi.fn>;
  };
}

/** Cena falsa: cada `show` fica pendente até o teste resolver ou rejeitar. */
function fakeScene(): FakeScene {
  let mountedHost: HTMLElement | undefined;
  let mountedOptions: MountOptions | undefined;
  const shows: ShowCall[] = [];
  const pending: Deferred[] = [];
  const handle = {
    turn: vi.fn(),
    resetPose: vi.fn(),
    setSpinning: vi.fn(),
    setReducedMotion: vi.fn(),
    dispose: vi.fn(),
  };
  const sceneHandle: SceneHandle = {
    ...handle,
    show: (goblin, signal) => {
      shows.push({ goblin, signal });
      return new Promise<void>((resolve, reject) => {
        pending.push({ resolve, reject });
      });
    },
  };
  return {
    shows,
    pending,
    handle,
    host: () => mountedHost,
    options: () => mountedOptions,
    loadScene: () =>
      Promise.resolve({
        mountScene: (host, options) => {
          mountedHost = host;
          mountedOptions = options;
          return sceneHandle;
        },
      }),
  };
}

function heroState(container: HTMLElement): string | null {
  return container.querySelector("[data-oficina-3d]")?.getAttribute("data-oficina-3d") ?? null;
}

function castButton(name: string): HTMLElement {
  return within(screen.getByRole("group", { name: TEXT.art.stage.castLabel })).getByRole("button", {
    name,
  });
}

async function settle(scene: FakeScene, index: number, error?: Error): Promise<void> {
  await act(async () => {
    if (error === undefined) {
      scene.pending[index]?.resolve();
    } else {
      scene.pending[index]?.reject(error);
    }
    await Promise.resolve();
  });
}

/** Monta com a cena falsa e conclui a carga da Rúbia. */
async function renderReady(scene: FakeScene, environment: Environment3D = READY) {
  const view = render(
    <OficinaHero environment={environment} whenPageIdle={runNow} loadScene={scene.loadScene} />,
  );
  await waitFor(() => {
    expect(scene.shows).toHaveLength(1);
  });
  await settle(scene, 0);
  expect(heroState(view.container)).toBe("pronto");
  return view;
}

describe("OficinaHero", () => {
  it("mostra a imagem estática da Rúbia primeiro, com texto alternativo e sem layout shift", () => {
    render(<OficinaHero environment={REDUCED_MOTION} whenPageIdle={never} />);

    const img = screen.getByRole("img", { name: /^Rúbia, goblin da oficina/ });
    expect(img).toHaveAttribute("width", "640");
    expect(img).toHaveAttribute("height", "800");
    expect(img).toHaveAttribute("fetchpriority", "high");
    expect(img).not.toHaveAttribute("loading");
  });

  it("o seletor tem as cinco goblins, na ordem do elenco, com a Rúbia escolhida", () => {
    render(<OficinaHero environment={REDUCED_MOTION} whenPageIdle={never} />);

    const buttons = within(
      screen.getByRole("group", { name: TEXT.art.stage.castLabel }),
    ).getAllByRole("button");
    expect(buttons.map((button) => button.textContent)).toEqual([
      "Rúbia",
      "Fagulha",
      "Ferrugem",
      "Jade",
      "Nyx",
    ]);
    expect(buttons.map((button) => button.getAttribute("aria-pressed"))).toEqual([
      "true",
      "false",
      "false",
      "false",
      "false",
    ]);
  });

  it("com movimento reduzido nem baixa a cena, mas o seletor troca a imagem", async () => {
    const loadScene = vi.fn(() => fakeScene().loadScene());
    const { container } = render(
      <OficinaHero environment={REDUCED_MOTION} whenPageIdle={runNow} loadScene={loadScene} />,
    );

    await userEvent.click(castButton("Nyx"));

    expect(loadScene).not.toHaveBeenCalled();
    expect(heroState(container)).toBe("imagem");
    expect(screen.getByRole("img", { name: /^Nyx, goblin/ })).toBeVisible();
    expect(castButton("Nyx")).toHaveAttribute("aria-pressed", "true");
    expect(castButton("Rúbia")).toHaveAttribute("aria-pressed", "false");
    expect(screen.queryByRole("button", { name: TEXT.art.stage.spin })).toBeNull();
    expect(screen.queryByRole("application")).toBeNull();
  });

  it("monta a cena num espaço decorativo e carrega só a Rúbia", async () => {
    const scene = fakeScene();
    const { container } = await renderReady(scene);

    expect(scene.shows.map((call) => call.goblin)).toEqual(["rubia"]);
    expect(scene.host()).toHaveAttribute("aria-hidden", "true");
    expect(container.querySelector("[data-goblin]")).toHaveAttribute("data-goblin", "rubia");
  });

  it("pronta, o palco entra no Tab com nome e instruções", async () => {
    const scene = fakeScene();
    await renderReady(scene);

    const stage = screen.getByRole("application", { name: "Rúbia em 3D" });
    expect(stage).toHaveAttribute("tabindex", "0");
    expect(stage).toHaveAccessibleDescription(TEXT.art.stage.hint);
  });

  it("trocar de goblin carrega só o modelo dela, com a imagem dela enquanto carrega", async () => {
    const scene = fakeScene();
    const { container } = await renderReady(scene);

    await userEvent.click(castButton("Nyx"));

    expect(scene.shows.map((call) => call.goblin)).toEqual(["rubia", "nyx"]);
    expect(heroState(container)).toBe("carregando");
    expect(screen.getByRole("img", { name: /^Nyx, goblin/ })).toHaveClass("opacity-100");
    expect(screen.queryByRole("application")).toBeNull();

    await settle(scene, 1);

    expect(heroState(container)).toBe("pronto");
    expect(screen.getByRole("application", { name: "Nyx em 3D" })).toBeInTheDocument();
  });

  it("trocar de novo antes de chegar cancela a carga anterior", async () => {
    const scene = fakeScene();
    await renderReady(scene);

    await userEvent.click(castButton("Nyx"));
    await userEvent.click(castButton("Fagulha"));

    expect(scene.shows.map((call) => call.goblin)).toEqual(["rubia", "nyx", "fagulha"]);
    expect(scene.shows[1]?.signal.aborted).toBe(true);
    expect(scene.shows[2]?.signal.aborted).toBe(false);
  });

  it("se o modelo de uma goblin falhar, fica a imagem dela, e outra ainda carrega", async () => {
    const scene = fakeScene();
    const { container } = await renderReady(scene);

    await userEvent.click(castButton("Jade"));
    await settle(scene, 1, new Error("HTTP 404"));

    expect(heroState(container)).toBe("falhou");
    expect(screen.getByRole("img", { name: /^Jade, goblin/ })).toBeVisible();
    expect(screen.queryByRole("button", { name: TEXT.art.stage.spin })).toBeNull();
    expect(screen.queryByRole("button", { name: TEXT.art.stage.reset })).toBeNull();
    expect(scene.host()).not.toHaveClass("cursor-grab");
    expect(scene.host()).not.toHaveClass("touch-pan-y");

    await userEvent.click(castButton("Fagulha"));
    await settle(scene, 2);
    expect(heroState(container)).toBe("pronto");
  });

  it("com a cena mostrada, o palco aceita arrasto e tem Girar e Pose inicial", async () => {
    const scene = fakeScene();
    await renderReady(scene);

    expect(scene.host()).toHaveClass("cursor-grab", "touch-pan-y");
    expect(screen.getByRole("button", { name: TEXT.art.stage.spin })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: TEXT.art.stage.reset })).toBeInTheDocument();
  });

  it("enquanto a próxima goblin carrega, o palco não aceita arrasto nem mostra os controles", async () => {
    const scene = fakeScene();
    await renderReady(scene);

    await userEvent.click(castButton("Nyx"));

    expect(scene.host()).not.toHaveClass("cursor-grab");
    expect(screen.queryByRole("button", { name: TEXT.art.stage.reset })).toBeNull();
  });

  it("se o chunk da cena falhar, fica a imagem estática", async () => {
    const { container } = render(
      <OficinaHero
        environment={READY}
        whenPageIdle={runNow}
        loadScene={() => Promise.reject(new Error("chunk"))}
      />,
    );

    await waitFor(() => {
      expect(heroState(container)).toBe("falhou");
    });
    expect(screen.getByRole("img", { name: /^Rúbia/ })).toBeVisible();
  });

  it("se o contexto WebGL se perder, volta para a imagem e some com os controles", async () => {
    const scene = fakeScene();
    const { container } = await renderReady(scene);

    act(() => {
      scene.options()?.onContextLost();
    });

    expect(heroState(container)).toBe("falhou");
    expect(screen.queryByRole("button", { name: TEXT.art.stage.reset })).toBeNull();
  });

  it("se o contexto WebGL se perder girando, libera a cena na hora, e só uma vez", async () => {
    const scene = fakeScene();
    const { unmount } = await renderReady(scene);
    await userEvent.click(screen.getByRole("button", { name: TEXT.art.stage.spin }));

    act(() => {
      scene.options()?.onContextLost();
    });

    expect(scene.handle.dispose).toHaveBeenCalledOnce();
    unmount();
    expect(scene.handle.dispose).toHaveBeenCalledOnce();
  });

  it.each(["ArrowRight", "ArrowLeft", "ArrowUp", "ArrowDown"])(
    "com o foco no palco, %s gira ou inclina",
    async (key) => {
      const scene = fakeScene();
      await renderReady(scene);

      screen.getByRole("application").focus();
      await userEvent.keyboard(`{${key}}`);

      expect(scene.handle.turn).toHaveBeenCalledExactlyOnceWith(keyTurn(key));
    },
  );

  it("Home volta à pose inicial e para o giro", async () => {
    const scene = fakeScene();
    await renderReady(scene);
    await userEvent.click(screen.getByRole("button", { name: TEXT.art.stage.spin }));

    screen.getByRole("application").focus();
    await userEvent.keyboard("{Home}");

    expect(scene.handle.resetPose).toHaveBeenCalledOnce();
    expect(scene.handle.setSpinning).toHaveBeenLastCalledWith(false);
    expect(screen.getByRole("button", { name: TEXT.art.stage.spin })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
  });

  it("outras teclas no palco não giram", async () => {
    const scene = fakeScene();
    await renderReady(scene);

    screen.getByRole("application").focus();
    await userEvent.keyboard("{Enter}a ");

    expect(scene.handle.turn).not.toHaveBeenCalled();
    expect(scene.handle.resetPose).not.toHaveBeenCalled();
  });

  it("Girar liga e desliga a auto-rotação, com aria-pressed", async () => {
    const scene = fakeScene();
    await renderReady(scene);
    const spin = screen.getByRole("button", { name: TEXT.art.stage.spin });
    expect(spin).toHaveAttribute("aria-pressed", "false");

    await userEvent.click(spin);
    expect(spin).toHaveAttribute("aria-pressed", "true");
    expect(scene.handle.setSpinning).toHaveBeenLastCalledWith(true);

    await userEvent.click(spin);
    expect(spin).toHaveAttribute("aria-pressed", "false");
    expect(scene.handle.setSpinning).toHaveBeenLastCalledWith(false);
  });

  it("girando e então reduz o movimento: o giro para, o Girar some e a cena fica sem entrada", async () => {
    const scene = fakeScene();
    const motion = reducibleMotion();
    await renderReady(scene, motion.environment);
    await userEvent.click(screen.getByRole("button", { name: TEXT.art.stage.spin }));
    expect(scene.handle.setSpinning).toHaveBeenLastCalledWith(true);

    act(() => {
      motion.reduce();
    });

    expect(scene.handle.setSpinning).toHaveBeenLastCalledWith(false);
    expect(scene.handle.setReducedMotion).toHaveBeenLastCalledWith(true);
    expect(screen.queryByRole("button", { name: TEXT.art.stage.spin })).toBeNull();
    expect(screen.getByRole("button", { name: TEXT.art.stage.reset })).toBeInTheDocument();
  });

  it("sem movimento reduzido, a cena sabe que pode tocar a entrada", async () => {
    const scene = fakeScene();
    await renderReady(scene);

    expect(scene.handle.setReducedMotion).toHaveBeenLastCalledWith(false);
  });

  it("Pose inicial volta a goblin e para o giro", async () => {
    const scene = fakeScene();
    await renderReady(scene);
    await userEvent.click(screen.getByRole("button", { name: TEXT.art.stage.spin }));

    await userEvent.click(screen.getByRole("button", { name: TEXT.art.stage.reset }));

    expect(scene.handle.resetPose).toHaveBeenCalledOnce();
    expect(scene.handle.setSpinning).toHaveBeenLastCalledWith(false);
  });

  it("ao sair da página, cancela a carga e libera a cena", async () => {
    const scene = fakeScene();
    const { unmount } = await renderReady(scene);
    await userEvent.click(castButton("Ferrugem"));

    unmount();

    expect(scene.handle.dispose).toHaveBeenCalledOnce();
    expect(scene.shows[1]?.signal.aborted).toBe(true);
  });
});
