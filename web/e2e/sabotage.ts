// Devil's Advocate da suíte E2E (Testes 19): cada spec tem que falhar quando o defeito que ela
// vigia é plantado. Cada caso copia o dist/ já construído, planta um defeito (CSS, script ou
// arquivo trocado, sem tocar no código-fonte), serve a cópia numa porta própria e roda só a spec
// do caso. Cada execução precisa terminar com todos os testes selecionados executados e detectar
// o defeito em pelo menos um deles.
//
// Uso, depois de `npm run build` e `npm run build:notice-e2e`:
// npm run e2e:sabotage [-- trecho do nome do caso]
import { spawnSync } from "node:child_process";
import {
  cpSync,
  existsSync,
  mkdtempSync,
  readdirSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { inspectSabotageReport } from "./support/sabotageReport.ts";
import { selectCases } from "./support/selectCases.ts";

interface Case {
  readonly name: string;
  /** Argumentos do `playwright test`: arquivo da spec e, se preciso, `-g`. */
  readonly spec: readonly [string, ...string[]];
  readonly assertions: readonly string[];
  readonly plant: (dist: string) => void;
  readonly env?: Readonly<Record<string, string>>;
  /** Build de origem da cópia; sem ele, o `dist/` normal. */
  readonly dist?: string;
  /** Projeto do Playwright; sem ele, o desktop. */
  readonly project?: "desktop" | "mobile";
  /** Defeito num arquivo do repositório, desfeito ao fim do caso (o snippet do Nginx). */
  readonly sourceEdit?: {
    readonly file: string;
    readonly search: string;
    readonly replace: string;
  };
}

const DIST = "dist";
const NOTICE_DIST = "dist-notice";
const BUILD_COMMAND: Readonly<Record<string, string>> = {
  [DIST]: "npm run build",
  [NOTICE_DIST]: "npm run build:notice-e2e",
};
const FIRST_PORT = 4180;
const PLANTED_KEY = "CANARY-sabotagem-fixa";

const CASES: readonly Case[] = [
  {
    name: "contraste: texto secundário quase invisível no tema claro",
    spec: ["contrast.spec.ts", "-g", "tema light"],
    assertions: ["contrast-light"],
    plant: (dist) => {
      addStylesheet(dist, 'html[data-theme="light"] { --fg-muted: #d6d3d1; }');
    },
  },
  {
    name: "contraste: texto secundário quase invisível só no tema escuro",
    spec: ["contrast.spec.ts", "-g", "tema dark"],
    assertions: ["contrast-dark"],
    plant: (dist) => {
      addStylesheet(dist, 'html[data-theme="dark"] { --fg-muted: #44403c; }');
    },
  },
  {
    name: "foco: anel removido",
    spec: ["focus.spec.ts", "-g", "anel"],
    assertions: ["focus-ring-missing", "focus-file-picker"],
    plant: (dist) => {
      addStylesheet(dist, "*:focus-visible, *:focus-visible::after { outline: none !important; }");
    },
  },
  {
    name: "foco: anel de 1 px",
    spec: ["focus.spec.ts", "-g", "Oficina, tema light"],
    assertions: ["focus-ring-width"],
    plant: (dist) => {
      addStylesheet(dist, "*:focus-visible::after, *:focus-visible { outline-width: 1px; }");
    },
  },
  {
    name: "foco: anel da cor do fundo",
    spec: ["focus.spec.ts", "-g", "Oficina, tema dark"],
    assertions: ["focus-ring-contrast"],
    plant: (dist) => {
      // Especificidade acima do :root e do [data-theme="dark"] do tokens.css.
      addStylesheet(dist, "html:root[data-theme] { --ring: var(--bg); }");
    },
  },
  {
    name: "foco: anel do botão Escolher arquivos removido (o input oculto ainda tem o dele)",
    spec: ["focus.spec.ts", "-g", "Bancada vazia \\(CSV Janitor\\), tema light"],
    assertions: ["focus-file-picker"],
    plant: (dist) => {
      addStylesheet(dist, ".file-picker:has(:focus-visible) { outline: none !important; }");
    },
  },
  {
    name: "ordem do Tab: Executar com tabindex positivo",
    spec: ["focus.spec.ts", "-g", "Bancada: arquivos"],
    assertions: ["run-tab-order"],
    plant: (dist) => {
      addScript(
        dist,
        `new MutationObserver(() => {
          document.querySelector('button[type="submit"]')?.setAttribute("tabindex", "1");
        }).observe(document, { childList: true, subtree: true });`,
      );
    },
  },
  {
    name: "ordem do Tab: botões do diálogo invertidos no celular",
    spec: ["session.spec.ts", "-g", "375 px"],
    assertions: ["dialog-button-order"],
    plant: (dist) => {
      addStylesheet(dist, "dialog div:has(> button + button) { flex-direction: column-reverse; }");
    },
  },
  {
    name: "movimento reduzido: regra do base.css apagada",
    spec: ["motion.spec.ts", "-g", "movimento reduzido"],
    assertions: ["reduced-motion-css"],
    plant: (dist) => {
      replaceInCss(dist, "prefers-reduced-motion:reduce", "prefers-reduced-motion:x");
    },
  },
  {
    name: "movimento reduzido: chunk 3D pedido mesmo assim",
    spec: ["motion.spec.ts", "-g", "Oficina"],
    assertions: ["reduced-motion-3d"],
    plant: (dist) => {
      addScript(dist, 'fetch("/assets/oficina-3d-sabotagem.js").catch(() => undefined);');
    },
  },
  {
    name: "espaçamento de texto: título num contêiner de altura fixa que corta o excesso",
    spec: ["text-spacing.spec.ts", "-g", "Oficina: com o espaçamento"],
    assertions: ["text-heading-clipping"],
    plant: (dist) => {
      addStylesheet(dist, "main h1 { height: 1em; overflow: hidden; }");
    },
  },
  {
    // A guarda da folha: sem o `word-spacing` da 1.4.12 no body, a spec tem de cair antes de medir.
    // `html body` vence o `*` da folha adotada por especificidade, ambos `!important`.
    name: "espaçamento de texto: a folha da 1.4.12 não muda o word-spacing do body",
    spec: ["text-spacing.spec.ts", "-g", "Oficina: com o espaçamento"],
    assertions: ["text-word-spacing"],
    plant: (dist) => {
      addStylesheet(dist, "html body { word-spacing: 0px !important; }");
    },
  },
  {
    name: "tema: theme-init.js vazio (pisca no primeiro quadro)",
    spec: ["theme.spec.ts", "-g", "primeiro quadro"],
    assertions: ["theme-first-frame"],
    plant: (dist) => {
      writeFileSync(join(dist, "theme-init.js"), "");
    },
  },
  {
    name: "tema: a escolha não persiste",
    spec: ["theme.spec.ts", "-g", "persiste"],
    assertions: ["theme-persistence"],
    plant: (dist) => {
      addScript(
        dist,
        'addEventListener("pagehide", () => { localStorage.removeItem("gs-theme"); });',
      );
    },
  },
  {
    name: "tema: Esc não chega ao seletor (o painel fica aberto)",
    spec: ["theme.spec.ts", "-g", "Esc fecha o seletor"],
    assertions: ["theme-close-escape"],
    plant: (dist) => {
      addScript(
        dist,
        `document.addEventListener("keydown", (event) => {
          if (event.key === "Escape") {
            event.stopPropagation();
          }
        }, true);`,
      );
    },
  },
  {
    name: "tema: o seletor não percebe o foco saindo (painel cobre o foco no celular)",
    spec: ["theme.spec.ts", "-g", "sair do seletor com Tab"],
    assertions: ["theme-close-tab"],
    plant: (dist) => {
      addScript(
        dist,
        `document.addEventListener("focusout", (event) => {
          if (event.target instanceof Element && event.target.closest("details") !== null) {
            event.stopPropagation();
          }
        }, true);`,
      );
    },
  },
  {
    name: "reflow: nome longo da Bancada volta a ser cortado (truncate)",
    spec: ["reflow.spec.ts", "-g", "Bancada"],
    assertions: ["bench-name-clipping"],
    plant: (dist) => {
      // O que o `truncate` fazia antes da correção, agora sobre a classe que o substituiu.
      addStylesheet(
        dist,
        ".wrap-anywhere { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }",
      );
    },
  },
  {
    name: "reflow: nome longo da Execução sem quebra dentro da palavra",
    spec: ["reflow.spec.ts", "-g", "Execução"],
    assertions: ["run-name-reflow"],
    plant: (dist) => {
      addStylesheet(dist, ".wrap-anywhere { overflow-wrap: normal; }");
    },
  },
  {
    name: "aviso: o botão volta a se chamar só Fechar",
    spec: ["notice.spec.ts", "-g", "Fechar aviso esconde"],
    assertions: ["notice-close-name"],
    dist: NOTICE_DIST,
    env: { E2E_NOTICE: "1" },
    plant: (dist) => {
      // Sem o complemento para leitor de tela, o nome perde o que o botão fecha.
      addStylesheet(dist, 'section[aria-label^="Aviso"] button .sr-only { display: none; }');
    },
  },
  {
    name: "aviso: fechar deixa o foco cair no <body>",
    spec: ["notice.spec.ts", "-g", "Fechar aviso esconde"],
    assertions: ["notice-close-focus"],
    dist: NOTICE_DIST,
    env: { E2E_NOTICE: "1" },
    plant: (dist) => {
      // Desliga só o foco no título que vem do clique em Fechar, como se o Layout não o pedisse;
      // o foco da troca de rota (FocusHeading) continua.
      addScript(
        dist,
        `let dismissing = false;
        document.addEventListener("click", (event) => {
          if (event.target instanceof Element && event.target.closest('section[aria-label^="Aviso"] button') !== null) {
            dismissing = true;
            setTimeout(() => { dismissing = false; }, 0);
          }
        }, true);
        const focus = HTMLElement.prototype.focus;
        HTMLElement.prototype.focus = function (options) {
          if (dismissing && this.tagName === "H1") {
            return;
          }
          focus.call(this, options);
        };`,
      );
    },
  },
  {
    name: "CLS: aviso inserido no topo 200 ms depois do conteúdo",
    spec: ["layout-shift.spec.ts"],
    assertions: ["cls-regression", "cls-late-text"],
    plant: (dist) => {
      addScript(
        dist,
        `const observer = new MutationObserver(() => {
          const main = document.querySelector("main");
          if (main?.querySelector("h1")) {
            observer.disconnect();
            setTimeout(() => {
              const notice = document.createElement("p");
              notice.textContent = "aviso que empurra a página ".repeat(80);
              main.prepend(notice);
            }, 200);
          }
        });
        observer.observe(document, { childList: true, subtree: true });`,
      );
    },
  },
  // No celular a margem é estreita (CLS 0,068 contra o teto de 0,05): o caso roda nos dois projetos.
  ...(["desktop", "mobile"] as const).map((project): Case => ({
    name: `CLS: fonte do texto trocada quando chega depois da primeira pintura (${project})`,
    spec: ["layout-shift.spec.ts", "-g", "fonte do texto"],
    assertions: ["cls-late-text"],
    dist: NOTICE_DIST,
    project,
    env: { E2E_NOTICE: "1" },
    plant: (dist) => {
      replaceInCss(dist, "font-display:optional", "font-display:swap");
    },
  })),
  {
    name: "CSP: atributo style inline",
    spec: ["csp.spec.ts", "-g", "Oficina, tema light"],
    assertions: ["csp-inline-style"],
    plant: (dist) => {
      addScript(dist, 'document.body.setAttribute("style", "outline: 1px solid red");');
    },
  },
  {
    name: "CSP: snippet do Nginx afrouxado com 'unsafe-inline'",
    spec: ["csp.spec.ts", "-g", "snippet de produção"],
    assertions: ["csp-snippet"],
    plant: () => undefined,
    sourceEdit: {
      file: "../docker/nginx/snippets/spa-headers.conf",
      search: "style-src 'self'",
      replace: "style-src 'self' 'unsafe-inline'",
    },
  },
  ...(["desktop", "mobile"] as const).map((project): Case => ({
    name: `textos legais: título da seção rente ao topo depois do sumário (${project})`,
    spec: ["legal.spec.ts", "-g", "folga acima do título"],
    assertions: ["legal-anchor-gap"],
    project,
    plant: (dist) => {
      addStylesheet(dist, '[id^="secao-"] { scroll-margin-top: 0 !important; }');
    },
  })),
  {
    name: "textos legais: URL longa estoura a largura do celular",
    spec: ["legal.spec.ts", "-g", "/termos cabe na largura"],
    assertions: ["legal-page-width"],
    project: "mobile",
    plant: (dist) => {
      addStylesheet(dist, ".wrap-anywhere { overflow-wrap: normal !important; }");
    },
  },
  {
    name: "jornadas: botões de reordenar sumiram (só sobra o arrasto)",
    spec: ["journeys.spec.ts"],
    assertions: ["reorder-buttons-visible"],
    plant: (dist) => {
      addStylesheet(dist, "button[data-direction] { display: none !important; }");
    },
  },
  {
    // O jsdom não faz layout: o alvo de 44 px dos botões de passo só o E2E prova.
    name: "palco 3D: botões de passo sem o alvo mínimo de 44 px (min-h-11 e min-w-11)",
    spec: ["oficina-3d.spec.ts", "-g", "a 360 px"],
    assertions: ["scene-touch-target"],
    plant: (dist) => {
      addStylesheet(
        dist,
        'button[aria-label^="Girar para"], button[aria-label^="Inclinar para"] { min-width: 0 !important; min-height: 0 !important; }',
      );
    },
  },
  {
    name: "chave de API: o convite para entrar vira alerta",
    spec: ["api-key.spec.ts", "-g", "convida a entrar"],
    assertions: ["sign-in-prompt-role"],
    plant: (dist) => {
      addScript(
        dist,
        `new MutationObserver(() => {
          document.querySelector("section[aria-labelledby]")?.setAttribute("role", "alert");
        }).observe(document, { childList: true, subtree: true });`,
      );
    },
  },
  {
    name: "CSRF: a mutação sai sem o X-CSRF-Token",
    spec: ["csrf.spec.ts", "-g", "leva o X-CSRF-Token"],
    assertions: ["csrf-mutation-header"],
    plant: (dist) => {
      addScript(
        dist,
        `const original = window.fetch;
        window.fetch = (input, init = {}) => {
          const headers = new Headers(init.headers);
          headers.delete("X-CSRF-Token");
          return original(input, { ...init, headers });
        };`,
      );
    },
  },
  {
    name: "Acesso: o segredo da chave nova vai para o sessionStorage",
    spec: ["access.spec.ts", "-g", "aparece uma vez"],
    assertions: ["api-key-in-storage"],
    plant: (dist) => {
      addScript(
        dist,
        `new MutationObserver(() => {
          const field = document.querySelector("input[readonly]");
          if (field instanceof HTMLInputElement && field.value !== "") {
            sessionStorage.setItem("chave", field.value);
          }
        }).observe(document, { childList: true, subtree: true });`,
      );
    },
  },
  {
    name: "histórico: Carregar mais sumiu",
    spec: ["history.spec.ts", "-g", "Carregar mais"],
    assertions: ["history-load-more-visible"],
    plant: (dist) => {
      addStylesheet(dist, 'ul[aria-label="Execuções"] ~ button { display: none !important; }');
    },
  },
  {
    name: "histórico: o filtro não vai para a URL",
    spec: ["history.spec.ts", "-g", "filtrar por estado"],
    assertions: ["history-filter-url"],
    plant: (dist) => {
      addScript(
        dist,
        `const push = history.pushState.bind(history);
        history.pushState = (state, title, url) =>
          push(state, title, typeof url === "string" ? url.split("?")[0] : url);`,
      );
    },
  },
  {
    name: "histórico: o foco do Carregar mais não chega à linha nova",
    spec: ["history.spec.ts", "-g", "na última página"],
    assertions: ["history-load-more-focus"],
    plant: (dist) => {
      addScript(
        dist,
        `document.addEventListener("focusin", (event) => {
          if (event.target instanceof HTMLAnchorElement && event.target.closest("li")) {
            event.target.blur();
          }
        });`,
      );
    },
  },
  {
    name: "Bancada: o parágrafo do aviso de origem vira região viva que nasce com o texto",
    spec: ["reuse.spec.ts", "-g", "região viva que já existia"],
    assertions: ["reuse-live-region"],
    plant: (dist) => {
      addScript(
        dist,
        `new MutationObserver(() => {
          for (const paragraph of document.querySelectorAll("p")) {
            if (paragraph.textContent?.startsWith("Parâmetros da execução de")) {
              paragraph.setAttribute("role", "status");
            }
          }
        }).observe(document.documentElement, { childList: true, subtree: true });`,
      );
    },
  },
  {
    name: "tela que não abriu: o foco não chega ao título do erro",
    spec: ["route-failure.spec.ts"],
    assertions: ["route-error-focus"],
    plant: (dist) => {
      addScript(
        dist,
        `document.addEventListener("focusin", (event) => {
          if (event.target instanceof HTMLHeadingElement &&
              event.target.textContent === "Não foi possível abrir esta tela") {
            event.target.blur();
          }
        });`,
      );
    },
  },
  {
    name: "chave de API: canário embutido no bundle",
    spec: ["api-key.spec.ts", "-g", "não aparece"],
    assertions: ["api-key-in-bundle"],
    env: { GS_E2E_API_KEY: PLANTED_KEY },
    plant: (dist) => {
      appendToEntryScript(dist, `globalThis.__apiKey = "${PLANTED_KEY}";`);
    },
  },
];

function addStylesheet(dist: string, css: string): void {
  writeFileSync(join(dist, "sabotagem.css"), css);
  injectInHead(dist, '<link rel="stylesheet" href="/sabotagem.css">');
}

function addScript(dist: string, js: string): void {
  writeFileSync(join(dist, "sabotagem.js"), js);
  injectInHead(dist, '<script src="/sabotagem.js" defer></script>');
}

function injectInHead(dist: string, tag: string): void {
  const index = join(dist, "index.html");
  writeFileSync(index, readFileSync(index, "utf8").replace("</head>", `${tag}</head>`));
}

function replaceInCss(dist: string, search: string, replacement: string): void {
  const assets = join(dist, "assets");
  const changed = readdirSync(assets)
    .filter((file) => file.endsWith(".css"))
    .filter((file) => {
      const path = join(assets, file);
      const content = readFileSync(path, "utf8");
      writeFileSync(path, content.replaceAll(search, replacement));
      return content.includes(search);
    });
  if (changed.length === 0) {
    throw new Error(`nenhum CSS de ${assets} contém ${search}: a sabotagem não pegou`);
  }
}

function appendToEntryScript(dist: string, js: string): void {
  const entry = /src="\/(assets\/index-[^"]+\.js)"/.exec(
    readFileSync(join(dist, "index.html"), "utf8"),
  );
  if (entry?.[1] === undefined) {
    throw new Error("index.html sem o script de entrada do Vite");
  }
  const path = join(dist, entry[1]);
  writeFileSync(path, `${readFileSync(path, "utf8")}\n${js}\n`);
}

function runCase(sabotage: Case, port: number): boolean {
  const edit = sabotage.sourceEdit;
  const original = edit === undefined ? null : readFileSync(edit.file, "utf8");
  const work = mkdtempSync(join(tmpdir(), "goblinshift-sabotagem-"));
  const dist = join(work, "dist");
  const reportPath = join(work, "report.json");
  try {
    if (edit !== undefined && original !== null) {
      if (!original.includes(edit.search)) {
        throw new Error(`${edit.file} não contém ${edit.search}: a sabotagem não pegou`);
      }
      writeFileSync(edit.file, original.replace(edit.search, edit.replace));
    }
    const source = sabotage.dist ?? DIST;
    if (!existsSync(source)) {
      throw new Error(
        `${source}/ não existe: rode antes \`${BUILD_COMMAND[source] ?? "o build dele"}\``,
      );
    }
    cpSync(source, dist, { recursive: true });
    sabotage.plant(dist);
    const project = sabotage.project ?? "desktop";
    const result = spawnSync(
      "npx",
      [
        "playwright",
        "test",
        "--config=e2e/playwright.config.ts",
        `--project=${project}`,
        "--reporter=json",
        "--output",
        join(work, "test-results"),
        ...sabotage.spec,
      ],
      {
        stdio: "ignore",
        env: {
          ...process.env,
          ...sabotage.env,
          E2E_DIST_DIR: dist,
          E2E_PORT: String(port),
          PLAYWRIGHT_JSON_OUTPUT_FILE: reportPath,
          PLAYWRIGHT_JSON_ONLY_FAILURES: "false",
        },
      },
    );
    if (!existsSync(reportPath)) {
      console.error(`${sabotage.name}: relatório Playwright ausente`);
      return false;
    }
    try {
      const summary = inspectSabotageReport(
        JSON.parse(readFileSync(reportPath, "utf8")),
        result.status,
        { spec: sabotage.spec[0], project, assertions: sabotage.assertions },
      );
      console.log(JSON.stringify({ sabotage: sabotage.name, exitCode: result.status, ...summary }));
      return summary.caught;
    } catch {
      console.error(`${sabotage.name}: relatório Playwright inválido ou inconsistente`);
      return false;
    }
  } finally {
    try {
      if (edit !== undefined && original !== null) {
        writeFileSync(edit.file, original);
      }
    } finally {
      rmSync(work, { recursive: true, force: true });
    }
  }
}

// Um argumento filtra os casos pelo nome: `npm run e2e:sabotage -- CLS`.
const filter = process.argv[2] ?? "";
const selection = selectCases(CASES, filter);
if (!selection.found) {
  console.error(selection.message);
  process.exit(1);
}
const selected = selection.cases;
const escaped = selected.filter((sabotage, index) => !runCase(sabotage, FIRST_PORT + index));
if (escaped.length > 0) {
  console.error(
    `\n${String(escaped.length)} cenário(s) sem execução completa e detecção do defeito.`,
  );
  process.exit(1);
}
console.log(`\nOs ${String(selected.length)} defeitos plantados derrubaram a spec que os vigia.`);
