// Palco 3D da abertura (ADR 027, 030 e 038): a imagem estática da goblin escolhida chega com a
// página e fica no lugar sem WebGL acelerado, com movimento reduzido ou se a cena falhar; com GPU,
// a cena chega depois do `load`, sem CDN, sem violar a CSP e com a entrada parando em até 5 s.
// Trocar de goblin no seletor baixa só o GLB dela; mouse, setas e o botão Girar mexem a vista.
import AxeBuilder from "@axe-core/playwright";

import { E2E } from "../support/env";
import {
  HERO,
  CHUNK_OR_MODEL,
  SCENE_READY_MS,
  INTRO_LIMIT_MS,
  pretendGpu,
  withoutWebGl,
  recordRequests,
  waitPastIdle,
  heroImage,
  castButton,
  modelsRequested,
  waitSceneStill,
  sceneHost,
  holdFrames,
  nextFrame,
  prepareGpu,
} from "../support/oficina3d";
import { expect, test } from "../support/test";

test("com movimento reduzido fica a imagem e nada do 3D é pedido", async ({ page }) => {
  const requests = recordRequests(page);
  await pretendGpu(page);
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/");
  await waitPastIdle(page);

  await expect(page.locator(HERO)).toHaveAttribute("data-oficina-3d", "imagem");
  await expect(heroImage(page)).toBeVisible();
  await expect(page.getByRole("button", { name: "Girar" })).toHaveCount(0);

  await castButton(page, "Nyx").click();
  await expect(castButton(page, "Nyx")).toHaveAttribute("aria-pressed", "true");
  await expect(heroImage(page, "Nyx")).toBeVisible();
  await waitPastIdle(page);
  expect(requests.map((r) => r.url()).filter((url) => CHUNK_OR_MODEL.test(url))).toEqual([]);
});

test("sem WebGL fica a imagem e nada do 3D é pedido", async ({ page, isMobile }) => {
  const requests = recordRequests(page);
  await withoutWebGl(page);
  await page.goto("/");
  await waitPastIdle(page);

  await expect(page.locator(HERO)).toHaveAttribute("data-oficina-3d", "imagem");
  await expect(heroImage(page)).toBeVisible();

  for (const name of ["Fagulha", "Ferrugem", "Jade", "Nyx", "Rúbia"]) {
    if (isMobile) await castButton(page, name).tap();
    else await castButton(page, name).click();
    await expect(heroImage(page, name)).toBeVisible();
    await expect(castButton(page, name)).toHaveAttribute("aria-pressed", "true");
  }
  await expect(page.getByRole("button", { name: /Inclinar para ver/ })).toHaveCount(0);
  expect(requests.map((r) => r.url()).filter((url) => CHUNK_OR_MODEL.test(url))).toEqual([]);
});

test("WebGL só em software conta como sem WebGL", async ({ page }) => {
  const requests = recordRequests(page);
  await pretendGpu(page, "SwiftShader");
  await page.goto("/");
  await waitPastIdle(page);

  await expect(page.locator(HERO)).toHaveAttribute("data-oficina-3d", "imagem");
  expect(requests.map((r) => r.url()).filter((url) => CHUNK_OR_MODEL.test(url))).toEqual([]);
});

test.describe("com GPU", () => {
  // O SwiftShader usa todos os núcleos do runner: com o fullyParallel, quatro cenas montando ao
  // mesmo tempo se estrangulavam até estourar o teto do teste (run 38871601000). Em fila, num
  // worker só, uma cena por projeto; o modo default, ao contrário do serial, não pula o resto
  // quando um teste falha.
  test.describe.configure({ mode: "default" });
  // Montar a cena em software leva mais que o teto padrão de 20 s da suíte.
  test.slow();
  // Cada quadro em software trava a thread principal; sob carga, um clique espera mais que 5 s.
  test.use({ actionTimeout: 15_000 });
  test.beforeEach(async ({ page, browserName }, testInfo) => {
    await prepareGpu(page, browserName, testInfo);
  });

  test("a cena chega depois do load, só da própria origem, e a entrada para em até 5 s", async ({
    page,
  }) => {
    const requests = recordRequests(page);
    const cspErrors: string[] = [];
    page.on("console", (message) => {
      if (message.type() === "error" && /Content Security Policy/i.test(message.text())) {
        cspErrors.push(message.text());
      }
    });
    await page.goto("/");

    const hero = page.locator(HERO);
    await expect(hero).toHaveAttribute("data-oficina-3d", "pronto", { timeout: SCENE_READY_MS });
    await expect(hero.locator("canvas")).toBeVisible();
    await expect(hero.locator("[data-movimento]")).toHaveAttribute("data-movimento", "parado", {
      timeout: INTRO_LIMIT_MS,
    });

    const origin = new URL(E2E.baseUrl).origin;
    const foreign = requests
      .map((r) => r.url())
      .filter((url) => !url.startsWith("blob:") && !url.startsWith("data:"))
      .filter((url) => new URL(url).origin !== origin);
    expect(foreign, "nenhum pedido a CDN ou outro domínio").toEqual([]);
    expect(cspErrors, "nenhuma violação da CSP com a cena montada").toEqual([]);

    const timing = await page.evaluate(() => {
      const navigation = performance.getEntriesByType("navigation")[0] as
        PerformanceNavigationTiming | undefined;
      return {
        loadEnd: navigation?.loadEventEnd ?? Number.POSITIVE_INFINITY,
        chunkStart: performance
          .getEntriesByType("resource")
          .filter((entry) => /\/assets\/oficina-3d[^/]*\.js$/.test(entry.name))
          .map((entry) => entry.startTime),
      };
    });
    expect(timing.chunkStart).toHaveLength(1);
    expect(timing.chunkStart[0] ?? 0).toBeGreaterThanOrEqual(timing.loadEnd);
    expect(modelsRequested(requests), "a abertura baixa só o modelo da Rúbia").toEqual(["rubia"]);
  });

  test("trocar de goblin carrega o GLB dela e só ele, com a imagem dela enquanto carrega", async ({
    page,
  }) => {
    const requests = recordRequests(page);
    await page.goto("/");
    const hero = page.locator(HERO);
    await waitSceneStill(page);

    // Segura o GLB da Nyx para ver a imagem dela no lugar enquanto ele não chega.
    let release = (): void => undefined;
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    await page.route(/oficina-3d-nyx-[^/]*\.glb$/, async (route) => {
      await held;
      await route.continue();
    });
    await castButton(page, "Nyx").click();
    await expect(hero).toHaveAttribute("data-oficina-3d", "carregando");
    await expect(heroImage(page, "Nyx")).toBeVisible();
    release();

    await expect(hero).toHaveAttribute("data-oficina-3d", "pronto", { timeout: SCENE_READY_MS });
    await expect(sceneHost(page)).toHaveAttribute("data-goblin", "nyx");
    await expect(page.getByRole("application", { name: "Nyx em 3D" })).toBeVisible();
    expect(modelsRequested(requests)).toEqual(["rubia", "nyx"]);
  });

  test("setas giram e inclinam dentro dos limites, e Home volta à pose inicial @keyboard", async ({
    page,
  }) => {
    await page.goto("/");
    await waitSceneStill(page);
    const host = sceneHost(page);
    await page.getByRole("application", { name: "Rúbia em 3D" }).focus();

    await page.keyboard.press("Home");
    await expect(host).toHaveAttribute("data-giro", "-20");
    await page.keyboard.press("ArrowRight");
    await expect(host).toHaveAttribute("data-giro", "-5");
    // De 0° a 35°: quatro passos de 10°, o último para no teto.
    for (let step = 0; step < 4; step += 1) {
      await page.keyboard.press("ArrowUp");
    }
    await expect(host).toHaveAttribute("data-inclinacao", "35");
    // De 35° a -15°: cinco passos de 10°.
    for (let step = 0; step < 5; step += 1) {
      await page.keyboard.press("ArrowDown");
    }
    await expect(host).toHaveAttribute("data-inclinacao", "-15");

    await page.keyboard.press("Home");
    await expect(host).toHaveAttribute("data-giro", "-20");
    await expect(host).toHaveAttribute("data-inclinacao", "0");
  });

  test("arrastar com o mouse gira e inclina a goblin", async ({ page }) => {
    await page.goto("/");
    const hero = page.locator(HERO);
    await waitSceneStill(page);
    const host = sceneHost(page);
    await page.getByRole("button", { name: "Pose inicial" }).click();
    await expect(host).toHaveAttribute("data-giro", "-20");

    const box = await hero.boundingBox();
    if (box === null) {
      throw new Error("palco sem caixa");
    }
    const centerX = box.x + box.width / 2;
    const centerY = box.y + box.height / 2;
    await page.mouse.move(centerX, centerY);
    await page.mouse.down();
    await page.mouse.move(centerX + 40, centerY + 20, { steps: 4 });
    await page.mouse.up();

    // 40 px a 0,01 rad/px giram ~23°; 20 px inclinam ~11°.
    await expect(host).toHaveAttribute("data-giro", "3");
    await expect(host).toHaveAttribute("data-inclinacao", "11");
  });

  test("botões inclinam sem arrasto dentro dos limites", async ({
    page,
    isMobile,
    browserName,
  }, testInfo) => {
    await page.goto("/");
    await waitSceneStill(page);
    const host = sceneHost(page);
    const up = page.getByRole("button", { name: "Inclinar para ver de cima" });
    const down = page.getByRole("button", { name: "Inclinar para ver de baixo" });
    await page.getByRole("button", { name: "Pose inicial" }).click();
    for (const button of [up, down]) {
      const box = await button.boundingBox();
      expect(box?.width).toBeGreaterThanOrEqual(44);
      expect(box?.height).toBeGreaterThanOrEqual(44);
    }
    for (let step = 0; step < 5; step += 1) {
      if (isMobile) await up.tap();
      else await up.click();
    }
    await expect(host).toHaveAttribute("data-inclinacao", "35");
    await expect(host).toHaveAttribute("data-giro", "-20");
    for (let step = 0; step < 6; step += 1) {
      if (isMobile) await down.tap();
      else await down.click();
    }
    await expect(host).toHaveAttribute("data-inclinacao", "-15");
    await page.getByRole("button", { name: "Pose inicial" }).click();
    await expect(host).toHaveAttribute("data-inclinacao", "0");
    await up.focus();
    await page.keyboard.press("Enter");
    await expect(host).toHaveAttribute("data-inclinacao", "10");
    await page.keyboard.press("Space");
    await expect(host).toHaveAttribute("data-inclinacao", "20");
    await expect(up).toBeFocused();
    await expect(up).toHaveCSS("outline-style", "solid");
    await expect(up).toHaveCSS("outline-width", "3px");
    // No WebKit/macOS, Option+Tab inclui os botões sem alterar a preferência do sistema.
    const tab = browserName === "webkit" && process.platform === "darwin" ? "Alt+Tab" : "Tab";
    await page.keyboard.press(tab);
    await expect(down).toBeFocused();
    await page.keyboard.press(`Shift+${tab}`);
    await expect(up).toBeFocused();
    await page.screenshot({
      path: testInfo.outputPath("controles-inclinacao.png"),
      fullPage: true,
    });
  });

  test("pinça no palco amplia a página; um dedo gira e rola", async ({
    page,
    isMobile,
    browserName,
  }) => {
    test.skip(!isMobile || browserName !== "chromium", "Pinça nativa via CDP exige Chromium móvel");
    await page.goto("/");
    await waitSceneStill(page);
    const host = sceneHost(page);
    await host.scrollIntoViewIfNeeded();
    await expect(host).toHaveCSS("touch-action", "pan-y pinch-zoom");
    const box = await host.boundingBox();
    if (box === null) throw new Error("palco sem caixa");
    const x = box.x + box.width / 2;
    const y = box.y + box.height / 2;
    const cdp = await page.context().newCDPSession(page);
    const point = (id: number, px: number, py: number) => ({ id, x: px, y: py });
    // Passa pelo reconhecedor de gestos nativo; não usa dispatchEvent.
    await cdp.send("Input.dispatchTouchEvent", {
      type: "touchStart",
      touchPoints: [point(1, x - 30, y), point(2, x + 30, y)],
    });
    for (let distance = 35; distance <= 85; distance += 5) {
      await cdp.send("Input.dispatchTouchEvent", {
        type: "touchMove",
        touchPoints: [point(1, x - distance, y), point(2, x + distance, y)],
      });
    }
    await cdp.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
    await expect
      .poll(() => page.evaluate(() => window.visualViewport?.scale ?? 1))
      .toBeGreaterThan(1);
    // O Chromium mantém o zoom ao recarregar. Restaura-o só depois de comprovar a pinça,
    // para que as coordenadas CSS do próximo gesto correspondam ao viewport de toque.
    await cdp.send("Emulation.setPageScaleFactor", { pageScaleFactor: 1 });
    await expect.poll(() => page.evaluate(() => window.visualViewport?.scale)).toBe(1);
    await page.reload();
    await waitSceneStill(page);
    await page.getByRole("button", { name: "Pose inicial" }).tap();
    await host.scrollIntoViewIfNeeded();
    const fresh = await host.boundingBox();
    if (fresh === null) throw new Error("palco sem caixa");
    const cx = fresh.x + fresh.width / 2;
    const cy = fresh.y + fresh.height / 2;
    await cdp.send("Input.dispatchTouchEvent", {
      type: "touchStart",
      touchPoints: [point(1, cx, cy)],
    });
    for (let delta = 5; delta <= 40; delta += 5) {
      await cdp.send("Input.dispatchTouchEvent", {
        type: "touchMove",
        touchPoints: [point(1, cx + delta, cy)],
      });
    }
    await cdp.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
    await expect(host).not.toHaveAttribute("data-giro", "-20");
    await expect(host).toHaveAttribute("data-inclinacao", "0");
    const before = await page.evaluate(() => window.scrollY);
    await cdp.send("Input.dispatchTouchEvent", {
      type: "touchStart",
      touchPoints: [point(1, cx, cy)],
    });
    for (let delta = 10; delta <= 90; delta += 10) {
      await cdp.send("Input.dispatchTouchEvent", {
        type: "touchMove",
        touchPoints: [point(1, cx, cy - delta)],
      });
    }
    await cdp.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
    await expect.poll(() => page.evaluate(() => window.scrollY)).toBeGreaterThan(before);
    await cdp.detach();
  });

  test("Girar liga e desliga a auto-rotação", async ({ page }) => {
    await page.goto("/");
    await waitSceneStill(page);
    await holdFrames(page);
    const spin = page.getByRole("button", { name: "Girar" });
    const host = sceneHost(page);
    const before = await host.getAttribute("data-giro");

    await spin.click();
    await expect(spin).toHaveAttribute("aria-pressed", "true");
    await expect(host).toHaveAttribute("data-movimento", "girando");
    // O primeiro quadro do giro marca o tempo; o segundo já anda.
    await nextFrame(page);
    await nextFrame(page);
    await expect(host).not.toHaveAttribute("data-giro", before ?? "");

    await spin.click();
    await expect(spin).toHaveAttribute("aria-pressed", "false");
    await expect(host).toHaveAttribute("data-movimento", "parado");
    // Desligado, nenhum quadro a mais mexe a goblin.
    const stopped = await host.getAttribute("data-giro");
    await nextFrame(page);
    await nextFrame(page);
    await expect(host).toHaveAttribute("data-giro", stopped ?? "");
  });

  test("movimento reduzido ligado depois da carga: some o Girar e a próxima goblin chega sem entrada", async ({
    page,
  }) => {
    await page.goto("/");
    await waitSceneStill(page);

    await page.emulateMedia({ reducedMotion: "reduce" });
    await expect(page.getByRole("button", { name: "Girar" })).toHaveCount(0);
    await castButton(page, "Nyx").click();

    await expect(page.locator(HERO)).toHaveAttribute("data-oficina-3d", "pronto", {
      timeout: SCENE_READY_MS,
    });
    const host = sceneHost(page);
    await expect(host).toHaveAttribute("data-goblin", "nyx");
    // Com a entrada, a goblin chegaria girando a partir de 52°; sem ela, já na pose de repouso.
    await expect(host).toHaveAttribute("data-movimento", "parado");
    await expect(host).toHaveAttribute("data-giro", "-20");
  });

  test("trocar de goblin e voltar devolve a GPU ao mesmo número de geometrias e texturas", async ({
    page,
  }) => {
    await page.goto("/");
    await waitSceneStill(page);
    const host = sceneHost(page);
    const gpuMemory = async () => ({
      geometries: await host.getAttribute("data-geometrias"),
      textures: await host.getAttribute("data-texturas"),
    });
    const withRubia = await gpuMemory();
    expect(Number(withRubia.geometries)).toBeGreaterThan(0);

    await castButton(page, "Nyx").click();
    await expect(host).toHaveAttribute("data-goblin", "nyx", { timeout: SCENE_READY_MS });
    await waitSceneStill(page);
    await castButton(page, "Rúbia").click();
    await expect(host).toHaveAttribute("data-goblin", "rubia", { timeout: SCENE_READY_MS });
    await waitSceneStill(page);

    expect(await gpuMemory()).toEqual(withRubia);
  });

  test("com a cena montada, o canvas fica fora do leitor de tela, o palco entra no Tab com nome, e o axe passa", async ({
    page,
  }) => {
    await page.goto("/");
    const hero = page.locator(HERO);
    await expect(hero).toHaveAttribute("data-oficina-3d", "pronto", { timeout: SCENE_READY_MS });

    const canvas = hero.locator("canvas");
    await expect(canvas.locator("xpath=..")).toHaveAttribute("aria-hidden", "true");
    expect(await canvas.evaluate((element) => element.tabIndex)).toBe(-1);
    await expect(hero).toHaveAttribute("role", "application");
    await expect(hero).toHaveAttribute("tabindex", "0");
    await expect(hero).toHaveAccessibleName("Rúbia em 3D");
    await expect(heroImage(page)).toBeAttached();

    const results = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
      .analyze();
    expect(results.violations.map((violation) => violation.id)).toEqual([]);
  });

  test("se o modelo não vier, fica a imagem", async ({ page }) => {
    await page.route(/\.glb(\?|$)/, (route) => route.fulfill({ status: 404, body: "" }));
    await page.goto("/");

    await expect(page.locator(HERO)).toHaveAttribute("data-oficina-3d", "falhou", {
      timeout: SCENE_READY_MS,
    });
    await expect(heroImage(page)).toBeVisible();
  });
});
