import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { Environment3D } from "./conditions";
import { OficinaHero, type MountOptions, type SceneModule } from "./OficinaHero";

const READY: Environment3D = {
  prefersReducedMotion: () => false,
  savesData: () => false,
  supportsWebGL2: () => true,
};

const REDUCED_MOTION: Environment3D = { ...READY, prefersReducedMotion: () => true };

function runNow(run: () => void): () => void {
  run();
  return () => undefined;
}

interface FakeScene {
  readonly loadScene: () => Promise<SceneModule>;
  readonly mounts: { host: HTMLElement; options: MountOptions }[];
  readonly dispose: ReturnType<typeof vi.fn>;
}

function fakeScene(mount: () => Promise<void> = () => Promise.resolve()): FakeScene {
  const mounts: FakeScene["mounts"] = [];
  const dispose = vi.fn();
  return {
    mounts,
    dispose,
    loadScene: () =>
      Promise.resolve({
        mountScene: async (host, options) => {
          mounts.push({ host, options });
          await mount();
          return { dispose };
        },
      }),
  };
}

function heroState(container: HTMLElement): string | null {
  return container.querySelector("[data-oficina-3d]")?.getAttribute("data-oficina-3d") ?? null;
}

describe("OficinaHero", () => {
  it("mostra a imagem estática primeiro, com texto alternativo e sem layout shift", () => {
    render(<OficinaHero environment={REDUCED_MOTION} whenPageIdle={() => () => undefined} />);

    const img = screen.getByRole("img", { name: /^Rúbia, goblin da oficina/ });
    expect(img).toHaveAttribute("width", "640");
    expect(img).toHaveAttribute("height", "800");
    expect(img).toHaveAttribute("fetchpriority", "high");
    expect(img).not.toHaveAttribute("loading");
  });

  it("com movimento reduzido nem baixa a cena 3D", () => {
    const loadScene = vi.fn(() => fakeScene().loadScene());
    const { container } = render(
      <OficinaHero environment={REDUCED_MOTION} whenPageIdle={runNow} loadScene={loadScene} />,
    );

    expect(loadScene).not.toHaveBeenCalled();
    expect(heroState(container)).toBe("imagem");
  });

  it("monta a cena num espaço decorativo, fora do leitor de tela, e marca pronto", async () => {
    const scene = fakeScene();
    const { container } = render(
      <OficinaHero environment={READY} whenPageIdle={runNow} loadScene={scene.loadScene} />,
    );

    await waitFor(() => {
      expect(heroState(container)).toBe("pronto");
    });
    const host = scene.mounts[0]?.host;
    expect(host).toHaveAttribute("aria-hidden", "true");
    expect(screen.getByRole("img", { name: /^Rúbia/ })).toBeInTheDocument();
  });

  it("se a cena falhar, fica a imagem estática", async () => {
    const scene = fakeScene(() => Promise.reject(new Error("GLB inválido")));
    const { container } = render(
      <OficinaHero environment={READY} whenPageIdle={runNow} loadScene={scene.loadScene} />,
    );

    await waitFor(() => {
      expect(heroState(container)).toBe("falhou");
    });
    expect(screen.getByRole("img", { name: /^Rúbia/ })).toBeVisible();
  });

  it("se o contexto WebGL se perder, volta para a imagem", async () => {
    const scene = fakeScene();
    const { container } = render(
      <OficinaHero environment={READY} whenPageIdle={runNow} loadScene={scene.loadScene} />,
    );
    await waitFor(() => {
      expect(heroState(container)).toBe("pronto");
    });

    scene.mounts[0]?.options.onContextLost();

    await waitFor(() => {
      expect(heroState(container)).toBe("falhou");
    });
  });

  it("ao sair da página, cancela a carga e libera a cena", async () => {
    const scene = fakeScene();
    const { container, unmount } = render(
      <OficinaHero environment={READY} whenPageIdle={runNow} loadScene={scene.loadScene} />,
    );
    await waitFor(() => {
      expect(heroState(container)).toBe("pronto");
    });

    unmount();

    expect(scene.dispose).toHaveBeenCalledOnce();
    expect(scene.mounts[0]?.options.signal.aborted).toBe(true);
  });
});
