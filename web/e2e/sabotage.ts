// Devil's Advocate da suíte E2E (Testes 19): cada spec tem que falhar quando o defeito que ela
// vigia é plantado. Cada caso copia o dist/ já construído, planta um defeito (CSS, script ou
// arquivo trocado, sem tocar no código-fonte), serve a cópia numa porta própria e roda só a spec
// do caso. O script sai com 1 se alguma spec continuar verde com o defeito.
//
// Uso, depois de `npm run build`: npm run e2e:sabotage [-- trecho do nome do caso]
import { spawnSync } from "node:child_process";
import { cpSync, mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

interface Case {
  readonly name: string;
  /** Argumentos do `playwright test`: arquivo da spec e, se preciso, `-g`. */
  readonly spec: readonly string[];
  readonly plant: (dist: string) => void;
  readonly env?: Readonly<Record<string, string>>;
  /** Defeito num arquivo do repositório, desfeito ao fim do caso (o snippet do Nginx). */
  readonly sourceEdit?: {
    readonly file: string;
    readonly search: string;
    readonly replace: string;
  };
}

const DIST = "dist";
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
    cpSync(DIST, dist, { recursive: true });
    sabotage.plant(dist);
    const result = spawnSync(
      "npx",
      [
        "playwright",
        "test",
        "--config=e2e/playwright.config.ts",
        "--project=desktop",
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
