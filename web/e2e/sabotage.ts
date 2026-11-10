// Devil's Advocate da suíte E2E (Testes 19): cada spec tem que falhar quando o defeito que ela
// vigia é plantado. Cada caso copia o dist/ já construído, planta um defeito (CSS, script ou
// arquivo trocado, sem tocar no código-fonte), serve a cópia numa porta própria e roda só a spec
// do caso. O script sai com 1 se alguma spec continuar verde com o defeito.
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

interface Case {
  readonly name: string;
  /** Argumentos do `playwright test`: arquivo da spec e, se preciso, `-g`. */
  readonly spec: readonly string[];
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
    plant: (dist) => {
      addStylesheet(dist, 'html[data-theme="light"] { --fg-muted: #d6d3d1; }');
    },
  },
  {
    name: "contraste: texto secundário quase invisível só no tema escuro",
    spec: ["contrast.spec.ts", "-g", "tema dark"],
    plant: (dist) => {
      addStylesheet(dist, 'html[data-theme="dark"] { --fg-muted: #44403c; }');
    },
  },
  {
    name: "foco: anel removido",
    spec: ["focus.spec.ts", "-g", "anel"],
    plant: (dist) => {
      addStylesheet(dist, "*:focus-visible, *:focus-visible::after { outline: none !important; }");
    },
  },
  {
    name: "foco: anel de 1 px",
    spec: ["focus.spec.ts", "-g", "Oficina, tema light"],
    plant: (dist) => {
      addStylesheet(dist, "*:focus-visible::after, *:focus-visible { outline-width: 1px; }");
    },
  },
  {
    name: "foco: anel da cor do fundo",
    spec: ["focus.spec.ts", "-g", "Oficina, tema dark"],
    plant: (dist) => {
      // Especificidade acima do :root e do [data-theme="dark"] do tokens.css.
      addStylesheet(dist, "html:root[data-theme] { --ring: var(--bg); }");
    },
  },
  {
    name: "foco: anel do botão Escolher arquivos removido (o input oculto ainda tem o dele)",
    spec: ["focus.spec.ts", "-g", "Bancada vazia \\(CSV Janitor\\), tema light"],
    plant: (dist) => {
      addStylesheet(dist, ".file-picker:has(:focus-visible) { outline: none !important; }");
    },
  },
  {
    name: "ordem do Tab: Executar com tabindex positivo",
    spec: ["focus.spec.ts", "-g", "Bancada: arquivos"],
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
    plant: (dist) => {
      addStylesheet(dist, "dialog div:has(> button + button) { flex-direction: column-reverse; }");
    },
  },
  {
    name: "movimento reduzido: regra do base.css apagada",
    spec: ["motion.spec.ts", "-g", "movimento reduzido"],
    plant: (dist) => {
      replaceInCss(dist, "prefers-reduced-motion:reduce", "prefers-reduced-motion:x");
    },
  },
  {
    name: "movimento reduzido: chunk 3D pedido mesmo assim",
    spec: ["motion.spec.ts", "-g", "Oficina"],
    plant: (dist) => {
      addScript(dist, 'fetch("/assets/oficina-3d-sabotagem.js").catch(() => undefined);');
    },
  },
  {
    name: "espaçamento de texto: título num contêiner de altura fixa que corta o excesso",
    spec: ["text-spacing.spec.ts", "-g", "Oficina: com o espaçamento"],
    plant: (dist) => {
      addStylesheet(dist, "main h1 { height: 1em; overflow: hidden; }");
    },
  },
  {
    // A guarda da folha: sem o `word-spacing` da 1.4.12 no body, a spec tem de cair antes de medir.
    // `html body` vence o `*` da folha adotada por especificidade, ambos `!important`.
    name: "espaçamento de texto: a folha da 1.4.12 não muda o word-spacing do body",
    spec: ["text-spacing.spec.ts", "-g", "Oficina: com o espaçamento"],
    plant: (dist) => {
      addStylesheet(dist, "html body { word-spacing: 0px !important; }");
    },
  },
  {
    name: "tema: theme-init.js vazio (pisca no primeiro quadro)",
    spec: ["theme.spec.ts", "-g", "primeiro quadro"],
    plant: (dist) => {
      writeFileSync(join(dist, "theme-init.js"), "");
    },
  },
  {
    name: "tema: a escolha não persiste",
    spec: ["theme.spec.ts", "-g", "persiste"],
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
    plant: (dist) => {
      addStylesheet(dist, ".wrap-anywhere { overflow-wrap: normal; }");
    },
  },
  {
    name: "aviso: o botão volta a se chamar só Fechar",
    spec: ["notice.spec.ts", "-g", "Fechar aviso esconde"],
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
    plant: (dist) => {
      addScript(dist, 'document.body.setAttribute("style", "outline: 1px solid red");');
    },
  },
  {
    name: "CSP: snippet do Nginx afrouxado com 'unsafe-inline'",
    spec: ["csp.spec.ts", "-g", "snippet de produção"],
    plant: () => undefined,
    sourceEdit: {
      file: "../docker/nginx/snippets/spa-headers.conf",
      search: "style-src 'self'",
      replace: "style-src 'self' 'unsafe-inline'",
    },
  },
  {
    name: "jornadas: botões de reordenar sumiram (só sobra o arrasto)",
    spec: ["journeys.spec.ts"],
    plant: (dist) => {
      addStylesheet(dist, "button[data-direction] { display: none !important; }");
    },
  },
  {
    name: "chave de API: o convite para entrar vira alerta",
    spec: ["api-key.spec.ts", "-g", "convida a entrar"],
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
    plant: (dist) => {
      addStylesheet(dist, 'ul[aria-label="Execuções"] ~ button { display: none !important; }');
    },
  },
  {
    name: "histórico: o filtro não vai para a URL",
    spec: ["history.spec.ts", "-g", "filtrar por estado"],
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
  const dist = mkdtempSync(join(tmpdir(), "goblinshift-sabotagem-"));
  const edit = sabotage.sourceEdit;
  const original = edit === undefined ? null : readFileSync(edit.file, "utf8");
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
    const result = spawnSync(
      "npx",
      [
        "playwright",
        "test",
        "--config=e2e/playwright.config.ts",
        `--project=${sabotage.project ?? "desktop"}`,
        "--reporter=line",
        ...sabotage.spec,
      ],
      {
        encoding: "utf8",
        env: { ...process.env, ...sabotage.env, E2E_DIST_DIR: dist, E2E_PORT: String(port) },
      },
    );
    const summary = /\d+ failed[\s\S]*?(\d+ passed)?/.exec(result.stdout)?.[0] ?? "nenhuma falha";
    const caught = result.status !== 0 && /\d+ failed/.test(result.stdout);
    console.log(
      `${caught ? "pegou " : "PASSOU"}  ${sabotage.name}: ${summary.replace(/\s+/g, " ")}`,
    );
    return caught;
  } finally {
    if (edit !== undefined && original !== null) {
      writeFileSync(edit.file, original);
    }
    rmSync(dist, { recursive: true, force: true });
  }
}

// Um argumento filtra os casos pelo nome: `npm run e2e:sabotage -- CLS`.
const filter = process.argv[2] ?? "";
const selected = CASES.filter((sabotage) => sabotage.name.includes(filter));
const escaped = selected.filter((sabotage, index) => !runCase(sabotage, FIRST_PORT + index));
if (escaped.length > 0) {
  console.error(`\n${String(escaped.length)} defeito(s) passaram pela suíte.`);
  process.exit(1);
}
console.log(`\nOs ${String(selected.length)} defeitos plantados derrubaram a spec que os vigia.`);
