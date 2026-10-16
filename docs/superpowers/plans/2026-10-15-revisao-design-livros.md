# Plano de implementação: revisão do design pelos livros de UI

> **Nota:** os nomes `moss`, `brass` e `stone` deste plano são da paleta anterior; a atual é a Taverna escura ([ADR 032](../../adr/032-paleta-taverna-escura.md)).

> **Para agentes:** SUB-SKILL OBRIGATÓRIA: use superpowers:subagent-driven-development (recomendada) ou superpowers:executing-plans para executar este plano tarefa por tarefa. Os passos usam checkbox (`- [ ]`).

**Objetivo:** aplicar as decisões da spec ao sistema visual da SPA:
- pátina para sucesso;
- 3 níveis de elevação;
- escala de z-index;
- tema escuro sem script;
- correções de CSS;
- confirmação de remoção e "Remover todos".

**Arquitetura:**
- Tudo vive em `web/`. Os tokens mudam em `web/src/styles/tokens.css`, com três camadas: tons crus em `@theme static`, papéis em `:root`/`[data-theme="dark"]` e utilitários em `@theme inline`.
- As regras ficam travadas pelos testes que já existem (`contrast.test.ts`, `classScale.test.ts`) e por specs Playwright novas.
- A interação de remoção reaproveita o `ConfirmDialog` (`<dialog>` modal nativo), que já existe.

**Stack:** React 19, Tailwind v4, Vite (rolldown), Vitest com Testing Library, Playwright, culori.

**Spec:** `docs/superpowers/specs/2026-10-15-revisao-design-livros-design.md`

## Restrições globais

- **Base:** comece só depois do merge do PR #42 (goblins no site). Antes da Tarefa 1, faça `git fetch && git rebase origin/main` nesta branch. Se o #42 tiver mudado uma linha citada aqui, procure a classe pelo nome (`grep -rn "shadow-3" web/src`) em vez de confiar no número da linha.
- **Comandos:** rode tudo dentro de `web/`:
  - `npm test -- <arquivo>` (Vitest);
  - `npm run typecheck`, `npm run lint` e `npm run format:check`;
  - `npm run build && npm run budget`;
  - `npm run e2e -- <spec>`.
- **Valores do sistema:** só valores do sistema. O `classScale.test.ts` recusa espaçamento fora da escala, valor arbitrário `-[…]`, tom cru (`moss`, `brass`, `stone`, `ember` e, a partir da Tarefa 1, `patina`) em componente, `outline-none` e `style={…}`.
- **Textos:** todo texto de interface fica em `web/src/text.ts`, em pt-BR.
- **Commits:** em português, no estilo do histórico ("Web: …"), terminando com `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **PRs:** dois, nesta ordem.
  - **`design-tokens`:** Tarefas 1 a 6. Abra do commit da Tarefa 6, contra `main`.
  - **`design-interacao`:** Tarefas 7 e 8. Depois do merge do primeiro, saia de `origin/main` numa branch `design-interacao`.

  Cada PR termina com `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
- **Proibido:**
  - **Alvo de toque:** não mude o alvo de toque (44 px, `min-h-11`).
  - **Escalas e raio:** não mude a escala de espaço, a escala de tipo, os pesos nem o raio único (`rounded-lg` = 8 px).

## Foco da revisão

Cinco riscos que nenhum teste de tarefa cobre de propósito. A linha de cada um diz onde o teste foi parar.

1. **Remover durante o envio.** Remover um arquivo que ainda está enviando aborta o envio e não deixa erro na tela. O teste foi acrescentado na Tarefa 7.
2. **Clique duplo no Confirmar.** Confirmar duas vezes, ou confirmar depois de o arquivo sair por outro caminho, não quebra nem remove outro arquivo. O teste está na Tarefa 7 (alvo pelo `localId`, não pelo índice).
3. **"Remover todos" com um arquivo só.** Quando a lista cai para 1 arquivo, "Remover todos" some, e o diálogo, se estava aberto, mostra a contagem de quando foi aberto. O teste está na Tarefa 8.
4. **Tema escolhido à mão contra o fallback.** Com `theme-init.js` funcionando e o tema "Claro" escolhido num sistema escuro, o fallback do `@media` não pode vencer: ele só vale para `:root:not([data-theme])`. O teste está na Tarefa 4 (e2e com o script ativo e a escolha "Claro").
5. **Travamento da rolagem.** Fechado o diálogo, a rolagem volta. Um diálogo fechado pelo Esc não pode deixar o `overflow: hidden` preso. O teste está na Tarefa 8 (e2e abre, fecha com Esc e confere).

---

### Tarefa 1: rampa de pátina e papéis de sucesso

**Arquivos:**
- Modificar: `web/src/styles/tokens.css` (bloco `@theme static`, depois da rampa `ember`; papéis `--success-*` em `:root` e `[data-theme="dark"]`)
- Modificar: `web/src/styles/contrast.test.ts` (teste de famílias e teste novo de papel)
- Modificar: `web/src/styles/classScale.test.ts` (`RAW_COLOR` inclui `patina`)

**Interfaces:**
- Produz: `--color-patina-50…950`. `--success-bg` e `--success-fg` passam a apontar para a pátina. Os utilitários `bg-success-bg` e `text-success-fg` não mudam de nome (o `StatusBadge` continua igual).

- [ ] **Passo 1: escrever os testes que falham.** Em `contrast.test.ts`, no `describe("tons crus")`, troque o teste de famílias:

```ts
  it("existem as cinco famílias da paleta, com 11 tons cada", () => {
    const families = ["moss", "brass", "stone", "ember", "patina"].map(
      (family) => [...rawColors(css).keys()].filter((name) => name.startsWith(`${family}-`)).length,
    );
    expect(families).toEqual([11, 11, 11, 11, 11]);
  });
```

e acrescente, no fim do arquivo, antes de `colorOf`:

```ts
describe("papéis de cor", () => {
  // Musgo é a cor do que se clica (PUI): sucesso tem matiz próprio, a pátina.
  it.each(THEMES)("sucesso usa pátina no tema $name", ({ selector }) => {
    const block = declarationsBlock(css, selector);
    expect(block).toMatch(/--success-bg:\s*var\(--color-patina-\d+\)/);
    expect(block).toMatch(/--success-fg:\s*var\(--color-patina-\d+\)/);
  });
});

function declarationsBlock(source: string, selector: string): string {
  const start = source.indexOf(`${selector} {`);
  return source.slice(start, source.indexOf("}", start));
}
```

Em `classScale.test.ts`, no `RAW_COLOR`, troque `(?:moss|brass|stone|ember|white)` por `(?:moss|brass|stone|ember|patina|white)`. Depois acrescente `["text-patina-800", "tom cru em componente (use o papel semântico): text-patina-800"]` à tabela `recusa %s`.

- [ ] **Passo 2: rodar e ver falhar.**

Rode: `npm test -- src/styles/contrast.test.ts src/styles/classScale.test.ts`
Esperado: FAIL em "existem as cinco famílias" (`[11,11,11,11,0]`) e em "sucesso usa pátina".

- [ ] **Passo 3: implementar.** Em `tokens.css`, depois da última linha `--color-ember-950`:

```css

  /* Pátina: sucesso. Verde-azulado de cobre oxidado, matiz fixo em 180° (32° além do musgo
     mais escuro), para o sucesso não parecer botão (PUI: a primária é só do que se clica). */
  --color-patina-50: oklch(0.975 0.02 180);
  --color-patina-100: oklch(0.945 0.035 180);
  --color-patina-200: oklch(0.895 0.055 180);
  --color-patina-300: oklch(0.825 0.075 180);
  --color-patina-400: oklch(0.735 0.09 180);
  --color-patina-500: oklch(0.645 0.095 180);
  --color-patina-600: oklch(0.555 0.09 180);
  --color-patina-700: oklch(0.475 0.08 180);
  --color-patina-800: oklch(0.405 0.068 180);
  --color-patina-900: oklch(0.335 0.055 180);
  --color-patina-950: oklch(0.265 0.042 180);
```

No `:root`, troque `--success-bg: var(--color-moss-50);` por `--success-bg: var(--color-patina-50);` e `--success-fg: var(--color-moss-800);` por `--success-fg: var(--color-patina-800);`.

No `[data-theme="dark"]`, troque `--success-bg: var(--color-moss-950);` por `--success-bg: var(--color-patina-950);` e `--success-fg: var(--color-moss-200);` por `--success-fg: var(--color-patina-200);`.

No comentário de topo do arquivo, troque "(musgo, latão, pedra, brasa)" por "(musgo, latão, pedra, brasa, pátina)".

- [ ] **Passo 4: rodar e ver passar.**

Rode: `npm test -- src/styles/contrast.test.ts src/styles/classScale.test.ts`
Esperado: PASS, inclusive os pares `success-fg × success-bg/surface` nos dois temas e "patina-N cabe no sRGB".

Se algum tom reprovar em "cabe no sRGB", baixe o croma desse tom em 0,005 por vez até passar. Se um par de contraste reprovar, troque o tom do papel um passo para o extremo (por exemplo, 800 → 900) e anote o valor no commit.

- [ ] **Passo 5: commit.**

```bash
git add web/src/styles/tokens.css web/src/styles/contrast.test.ts web/src/styles/classScale.test.ts
git commit -m "Web: sucesso ganha a pátina, e o musgo fica só para o que se clica

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Tarefa 2: elevação em 3 níveis mais a rebaixada

**Arquivos:**
- Modificar: `web/src/styles/tokens.css`. Troque os blocos `--elevation-*` em `:root` e `[data-theme="dark"]` e as linhas `--shadow-*` de `@theme inline`.
- Modificar: `web/src/styles/classScale.test.ts`. Ganha uma regra nova de sombra.
- Modificar (troca de classe):
  - `web/src/app/HomePage.tsx`;
  - `web/src/app/Layout.tsx`;
  - `web/src/components/a11y/FileDropzone.tsx`;
  - `web/src/components/a11y/ReorderableList.tsx`;
  - `web/src/components/feedback/EmptyState.tsx`;
  - `web/src/components/ui/Button.tsx`;
  - `web/src/components/ui/Card.tsx`;
  - `web/src/components/ui/ConfirmDialog.tsx`;
  - `web/src/history/HistoryPage.tsx`;
  - `web/src/history/RunList.tsx`;
  - `web/src/theme/ThemeSwitcher.tsx`;
  - qualquer outro arquivo que o PR #42 tenha trazido com `shadow-<n>`. Confira com `grep -rnE "shadow-[0-9]" web/src`.

**Interfaces:**
- Produz os utilitários `shadow-card`, `shadow-float`, `shadow-dialog`, `shadow-sunken` e `shadow-none`. Deixam de existir `shadow-1` a `shadow-5`.

- [ ] **Passo 1: escrever o teste que falha.** Em `classScale.test.ts`, depois de `RAW_COLOR`, acrescente:

```ts
/** Três níveis de elevação mais a rebaixada (spec 2026-10-15, decisão 2). */
const ALLOWED_SHADOWS = new Set(["card", "float", "dialog", "sunken", "none"]);
const SHADOW_UTILITY = /(?<=^|[\s"'`:])shadow-([a-z0-9]+)(?=$|[\s"'`])/g;
```

Dentro de `styleViolations`, antes do `if (/\boutline-none\b/…)`, acrescente:

```ts
  for (const [match, level] of source.matchAll(SHADOW_UTILITY)) {
    if (!ALLOWED_SHADOWS.has(level ?? "")) {
      violations.push(`sombra fora dos níveis: ${match}`);
    }
  }
```

Na tabela `recusa %s`, acrescente `["shadow-3", "sombra fora dos níveis: shadow-3"]` e `["hover:shadow-md", "sombra fora dos níveis: shadow-md"]`. Na tabela `aceita %s`, troque `"data-[dragging=true]:shadow-3"` por `"data-[dragging=true]:shadow-float"` e acrescente `"shadow-card hover:shadow-float shadow-dialog shadow-sunken active:shadow-none"`.

- [ ] **Passo 2: rodar e ver falhar.**

Rode: `npm test -- src/styles/classScale.test.ts`
Esperado: FAIL em "recusa shadow-3" e nos testes "usa só valores do sistema" de cada arquivo listado acima.

- [ ] **Passo 3: implementar os tokens.**

1. Em `:root`, troque as cinco linhas `--elevation-1` a `--elevation-5` (mantenha `--elevation-sunken`) por:

```css
  /* Três níveis (spec 2026-10-15): cartão sobre a página, flutuante (menu, popover, arrasto) e
     diálogo. Duas partes, a luz vem de cima (RUI cap. 6); no diálogo a parte pequena some. */
  --elevation-card: 0 2px 4px oklch(0.17 0.013 55 / 0.12), 0 1px 2px oklch(0.17 0.013 55 / 0.2);
  --elevation-float: 0 10px 20px oklch(0.17 0.013 55 / 0.15), 0 3px 6px oklch(0.17 0.013 55 / 0.1);
  --elevation-dialog: 0 20px 40px oklch(0.17 0.013 55 / 0.2);
```

2. Em `[data-theme="dark"]`, troque as cinco linhas `--elevation-1` a `--elevation-5` por:

```css
  --elevation-card: inset 0 1px 0 oklch(1 0 0 / 0.06);
  --elevation-float: 0 10px 20px oklch(0 0 0 / 0.5);
  --elevation-dialog: 0 20px 40px oklch(0 0 0 / 0.6), inset 0 0 0 1px oklch(1 0 0 / 0.08);
```

3. Em `@theme inline`, troque as linhas `--shadow-1` a `--shadow-5` por:

```css
  --shadow-card: var(--elevation-card);
  --shadow-float: var(--elevation-float);
  --shadow-dialog: var(--elevation-dialog);
```

- [ ] **Passo 4: implementar a troca nas classes.** Rode o mapeamento abaixo em `web/src` e depois confira o diff à mão:

| De | Para |
|---|---|
| `shadow-1` | `shadow-card` |
| `shadow-2` | `shadow-card` |
| `shadow-3` | `shadow-float` |
| `shadow-4` | `shadow-float` |
| `shadow-5` | `shadow-dialog` |

```bash
cd web/src
grep -rlE "shadow-[1-5]\b" --exclude='*.test.*' . | while read -r f; do
  sed -i '' -E 's/shadow-[12]\b/shadow-card/g; s/shadow-[34]\b/shadow-float/g; s/shadow-5\b/shadow-dialog/g' "$f"
done
grep -rnE "shadow-[0-9]" . ; cd ../..
```

Esperado: o último `grep` não imprime nada, ou só linhas de `*.test.*`. Corrija essas linhas à mão com a mesma tabela.

Onde a troca virar `shadow-card hover:shadow-card` (no `RunList.tsx`, que era `shadow-1 hover:shadow-2`), troque o hover por `hover:shadow-float`. Assim a linha continua subindo ao passar o mouse.

Em `Card.tsx`, troque o comentário "sombra de nível 2 no claro" por "sombra de cartão no claro".

- [ ] **Passo 5: rodar e ver passar.**

Rode: `npm test`
Esperado: PASS em tudo.

Rode: `npm run typecheck && npm run lint && npm run format:check`
Esperado: sem erro.

- [ ] **Passo 6: commit.**

```bash
git add web/src
git commit -m "Web: elevação em três níveis (cartão, flutuante, diálogo) mais a rebaixada

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Tarefa 3: escala de z-index e altura mínima em `svh`

**Arquivos:**
- Modificar:
  - `web/src/styles/tokens.css` (`:root`);
  - `web/src/styles/classScale.test.ts`;
  - `web/src/app/Layout.tsx`: `min-h-screen` passa a `min-h-svh` e `focus:z-20` passa a `focus:z-(--z-skip)`;
  - `web/src/theme/ThemeSwitcher.tsx`: `z-10` passa a `z-(--z-menu)`.

**Interfaces:**
- Produz as variáveis `--z-menu` e `--z-skip`, que os componentes consomem como `z-(--z-menu)` e `z-(--z-skip)`.

- [ ] **Passo 1: escrever o teste que falha.** Em `classScale.test.ts`, acrescente perto das outras constantes:

```ts
/** Camadas com nome (CSSD 6.4.3); o <dialog> modal usa a top layer e não precisa de z-index. */
const Z_LAYERS = new Set(["--z-menu", "--z-skip"]);
const Z_UTILITY = /(?<=^|[\s"'`:])-?z-(\d+|\(([^)]*)\))(?=$|[\s"'`])/g;
const SCREEN_HEIGHT = /(?<=^|[\s"'`:])(?:min-|max-)?h-screen(?=$|[\s"'`])/g;
```

Dentro de `styleViolations`, acrescente:

```ts
  for (const [match, , variable] of source.matchAll(Z_UTILITY)) {
    if (variable === undefined || !Z_LAYERS.has(variable)) {
      violations.push(`z-index fora das camadas: ${match}`);
    }
  }
  for (const [match] of source.matchAll(SCREEN_HEIGHT)) {
    violations.push(`altura de janela em vh: ${match} (use svh)`);
  }
```

Na tabela `recusa %s`, acrescente:
- `["z-10", "z-index fora das camadas: z-10"]`;
- `["focus:z-(--qualquer)", "z-index fora das camadas: z-(--qualquer)"]`;
- `["min-h-screen", "altura de janela em vh: min-h-screen"]`.

Na tabela `aceita %s`, acrescente `"z-(--z-menu) focus:z-(--z-skip) min-h-svh"`.

- [ ] **Passo 2: rodar e ver falhar.**

Rode: `npm test -- src/styles/classScale.test.ts`
Esperado: FAIL nas três linhas novas da tabela `recusa` e em `Layout.tsx` e `ThemeSwitcher.tsx` ("usa só valores do sistema").

- [ ] **Passo 3: implementar.** Em `tokens.css`, no fim do `:root`, depois de `--duration-base: 200ms;`:

```css

  /* Camadas com nome (CSSD 6.4.3): só as que existem. O <dialog> modal fica na top layer. */
  --z-menu: 10;
  --z-skip: 20;
```

Em `Layout.tsx`, troque `min-h-screen` por `min-h-svh` e `focus:z-20` por `focus:z-(--z-skip)`. Em `ThemeSwitcher.tsx`, troque `z-10` por `z-(--z-menu)`.

- [ ] **Passo 4: rodar e ver passar.**

Rode: `npm test && npm run build`
Esperado: PASS. O build gera `z-index: var(--z-menu)`, o que dá para conferir com `grep -o "z-index:var(--z-menu)" dist/assets/*.css`.

- [ ] **Passo 5: commit.**

```bash
git add web/src
git commit -m "Web: z-index por camadas com nome e moldura em min-h-svh

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Tarefa 4: tema escuro mesmo sem `theme-init.js`

**Arquivos:**
- Modificar: `web/src/styles/tokens.css`, com o bloco de fallback depois de `[data-theme="dark"] { … }`.
- Modificar: `web/src/styles/contrast.test.ts`, com um teste de igualdade.
- Modificar: `web/e2e/specs/theme.spec.ts`, com dois testes.

**Interfaces:**
- Consome: o bloco `[data-theme="dark"]`, já com a pátina e as elevações das Tarefas 1 e 2.

- [ ] **Passo 1: escrever os testes que falham.** No fim de `contrast.test.ts`, antes de `colorOf`:

```ts
describe("fallback do tema escuro", () => {
  // Sem theme-init.js (bloqueado, falha de rede) não há data-theme, e o sistema escuro precisa
  // do mesmo bloco: as duas cópias não podem divergir.
  it("repete o bloco escuro, declaração por declaração", () => {
    const fallback = declarationLines(css, ":root:not([data-theme]) {");
    expect(fallback.length).toBeGreaterThan(20);
    expect(fallback).toEqual(declarationLines(css, '[data-theme="dark"] {'));
  });
});

function declarationLines(source: string, opening: string): string[] {
  const start = source.indexOf(opening);
  if (start === -1) {
    throw new Error(`tokens.css não tem o bloco ${opening}`);
  }
  const body = source.slice(start + opening.length, source.indexOf("}", start));
  return body
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.startsWith("--") || line.startsWith("color-scheme"));
}
```

Em `theme.spec.ts`, no fim:

```ts
test("sem theme-init.js, o sistema escuro ainda pinta a página no escuro", async ({ page }) => {
  await page.route("**/theme-init.js", (route) => route.abort());
  await page.emulateMedia({ colorScheme: "dark" });
  await page.goto("/");
  await expect(html(page)).not.toHaveAttribute("data-theme", /.+/);
  const background = await html(page).evaluate((element) => getComputedStyle(element).backgroundColor);
  const darkBackground = await page.evaluate(() => {
    document.documentElement.dataset.theme = "dark";
    const color = getComputedStyle(document.documentElement).backgroundColor;
    delete document.documentElement.dataset.theme;
    return color;
  });
  expect(background).toBe(darkBackground);
});

test("a escolha Claro vence o fallback num sistema escuro", async ({ page }) => {
  await page.emulateMedia({ colorScheme: "dark" });
  await page.goto("/");
  await chooseTheme(page, "Claro");
  const background = await html(page).evaluate((element) => getComputedStyle(element).backgroundColor);
  await page.emulateMedia({ colorScheme: "light" });
  await chooseTheme(page, "Sistema");
  const lightBackground = await html(page).evaluate((element) => getComputedStyle(element).backgroundColor);
  expect(background).toBe(lightBackground);
});
```

- [ ] **Passo 2: rodar e ver falhar.**

Rode: `npm test -- src/styles/contrast.test.ts`
Esperado: FAIL com "tokens.css não tem o bloco :root:not([data-theme]) {".

Rode: `npm run build && npm run e2e -- e2e/specs/theme.spec.ts`
Esperado: FAIL em "sem theme-init.js…", porque o fundo sai claro.

- [ ] **Passo 3: implementar.** Em `tokens.css`, logo depois do `}` que fecha `[data-theme="dark"]`, acrescente:

```css

/* Fallback sem theme-init.js: segue o sistema, como a opção Sistema. Cópia exata do bloco acima,
   garantida por contrast.test.ts ("fallback do tema escuro"); mude os dois juntos. */
@media (prefers-color-scheme: dark) {
  :root:not([data-theme]) {
```

Em seguida, copie **todas** as linhas de declaração do bloco `[data-theme="dark"]`, da `color-scheme: dark;` até a última `--elevation-sunken`, mantendo a ordem e o texto. Feche com:

```css
  }
}
```

Ponto que não muda: o `@custom-variant dark` continua ligado ao atributo. Classes `dark:` não valem no fallback, e o teste de igualdade cobre só os tokens, que é onde está a cor.

- [ ] **Passo 4: rodar e ver passar.**

Rode: `npm test && npm run build && npm run e2e -- e2e/specs/theme.spec.ts e2e/specs/contrast.spec.ts`
Esperado: PASS.

- [ ] **Passo 5: commit.**

```bash
git add web/src/styles web/e2e/specs/theme.spec.ts
git commit -m "Web: tema escuro do sistema vale mesmo sem o theme-init.js

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Tarefa 5: diálogo (rolagem travada e fundo escurecido)

**Arquivos:**
- Modificar: `web/src/styles/tokens.css`. O `--overlay` entra em `:root`, em `[data-theme="dark"]` e no fallback da Tarefa 4.
- Modificar: `web/src/styles/base.css`: a regra `dialog::backdrop` e a regra nova `html:has(dialog[open])`.
- Modificar: `web/src/styles/contrast.test.ts`.

**Interfaces:**
- Produz o papel `--overlay`. O e2e da rolagem está na Tarefa 8, que é onde a Bancada passa a abrir o diálogo.

- [ ] **Passo 1: escrever o teste que falha.** Em `contrast.test.ts`, no `describe("papéis de cor")`:

```ts
  it.each(THEMES)("o fundo escurecido do diálogo tem papel próprio no tema $name", ({ selector }) => {
    expect(semanticColors(css, selector).get("overlay")).toBeDefined();
  });
```

- [ ] **Passo 2: rodar e ver falhar.**

Rode: `npm test -- src/styles/contrast.test.ts`
Esperado: FAIL nos dois temas, porque `overlay` está `undefined`.

- [ ] **Passo 3: implementar.**

1. Em `tokens.css`, acrescente depois de `--hero-band`:
   - no `:root`: `--overlay: var(--color-stone-950);`
   - no `[data-theme="dark"]` e na cópia do fallback, na mesma posição: `--overlay: oklch(0 0 0);`
2. Em `base.css`, troque a regra do backdrop por:

```css
  /* Diálogo de confirmação: o fundo escurece para a página recuar (RUI cap. 6). Mistura em sRGB:
     em oklch o transparente pode puxar o matiz (CSSD 13.1.2). */
  dialog::backdrop {
    background-color: color-mix(in srgb, var(--overlay) 60%, transparent);
  }

  /* Com um diálogo aberto a página atrás não rola (CSSD 6.1.2); o gutter evita o salto quando a
     barra de rolagem some. */
  html:has(dialog[open]) {
    overflow: hidden;
    scrollbar-gutter: stable;
  }
```

- [ ] **Passo 4: rodar e ver passar.**

Rode: `npm test`
Esperado: PASS, inclusive o teste de igualdade do fallback, se o `--overlay` entrou nos dois blocos escuros.

- [ ] **Passo 5: commit.**

```bash
git add web/src/styles
git commit -m "Web: diálogo trava a rolagem e o fundo escurecido ganha papel próprio

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Tarefa 6: preload da fonte do título

**Arquivos:**
- Criar: `web/scripts/vite-preload-fonts.ts`
- Modificar:
  - `web/vite.config.ts` (`plugins`);
  - `web/scripts/check-budget.ts` (regra nova em `budgetViolations`);
  - `web/scripts/check-budget.test.ts`.

**Interfaces:**
- Produz `preloadFonts(pattern: RegExp): Plugin`, que injeta `<link rel="preload" as="font" type="font/woff2" crossorigin href="/assets/<hash>.woff2">` no `index.html` do build.
- Produz também `FONT_PRELOAD = /bricolage-grotesque-latin-wght-normal-[^/]*\.woff2$/`, exportado de `check-budget.ts` e usado pelos dois.

- [ ] **Passo 1: escrever os testes que falham.** Em `check-budget.test.ts`:

1. Troque a constante `INDEX` para incluir o preload:

```ts
const INDEX = `<script src="/theme-init.js"></script>
<link rel="preload" as="font" type="font/woff2" crossorigin href="/assets/bricolage-grotesque-latin-wght-normal-x.woff2">
<script type="module" crossorigin src="/assets/index-abc.js"></script>
<link rel="modulepreload" crossorigin href="/assets/vendor-def.js">
<link rel="stylesheet" crossorigin href="/assets/index-abc.css">`;
```

2. Acrescente:

```ts
  it("exige o preload da fonte do título", () => {
    const withoutPreload = INDEX.replace(/<link rel="preload"[^>]*>\n/, "");
    expect(budgetViolations(WITHIN, withoutPreload)).toContain(
      "index.html sem preload da fonte do título (bricolage-grotesque latin)",
    );
  });
```

- [ ] **Passo 2: rodar e ver falhar.**

Rode: `npm test -- scripts/check-budget.test.ts`
Esperado: FAIL em "exige o preload…".

- [ ] **Passo 3: implementar a regra.**

1. Em `check-budget.ts`, exporte a constante:

```ts
/** A fonte do título da primeira tela: a única com preload (CSSD 12.5); o resto chega por swap. */
export const FONT_PRELOAD = /bricolage-grotesque-latin-wght-normal-[^/]*\.woff2$/;
```

2. Em `budgetViolations`, antes do `for (const file of files)`, acrescente:

```ts
  if (!hasFontPreload(indexHtml)) {
    violations.push("index.html sem preload da fonte do título (bricolage-grotesque latin)");
  }
```

3. Acrescente no fim do arquivo, junto das outras funções:

```ts
function hasFontPreload(indexHtml: string): boolean {
  return [...indexHtml.matchAll(/<link\b[^>]*rel="preload"[^>]*>/g)].some(([tag]) => {
    const href = /href="\/([^"]+)"/.exec(tag)?.[1];
    return href !== undefined && FONT_PRELOAD.test(href) && tag.includes("crossorigin");
  });
}
```

Se `violations` for `const` com um array literal, empurrar elemento funciona do mesmo jeito. Se for `readonly`, troque o tipo para `string[]`.

- [ ] **Passo 4: implementar o plugin.** Crie `web/scripts/vite-preload-fonts.ts`:

```ts
import type { Plugin } from "vite";

/**
 * Injeta o preload das fontes cujo arquivo final casa com `pattern`. O nome tem hash do build, por
 * isso o link não pode ir escrito no index.html.
 */
export function preloadFonts(pattern: RegExp): Plugin {
  return {
    name: "goblinshift-preload-fonts",
    apply: "build",
    transformIndexHtml: {
      order: "post",
      handler(_html, context) {
        const bundle = context.bundle ?? {};
        return Object.keys(bundle)
          .filter((fileName) => pattern.test(fileName))
          .map((fileName) => ({
            tag: "link",
            attrs: {
              rel: "preload",
              as: "font",
              type: "font/woff2",
              crossorigin: "",
              href: `/${fileName}`,
            },
            injectTo: "head-prepend" as const,
          }));
      },
    },
  };
}
```

Em `vite.config.ts`:
- acrescente `import { FONT_PRELOAD } from "./scripts/check-budget";`;
- acrescente `import { preloadFonts } from "./scripts/vite-preload-fonts";`;
- troque `plugins: [react(), tailwindcss()],` por `plugins: [react(), tailwindcss(), preloadFonts(FONT_PRELOAD)],`.

Se o `tsconfig.node.json` não incluir `scripts/`, acrescente `"scripts/vite-preload-fonts.ts"` ao `include`.

Esse plugin não tem teste unitário próprio. Quem confere o resultado no build real é o `npm run budget`.

- [ ] **Passo 5: rodar e ver passar.**

Rode: `npm test -- scripts/check-budget.test.ts && npm run typecheck && npm run build && npm run budget && grep -c 'rel="preload" as="font"' dist/index.html`
Esperado: PASS, budget sem violações e `1` no `grep`.

Rode: `npm run e2e -- e2e/specs/csp.spec.ts`
Esperado: PASS. O preload é da própria origem.

- [ ] **Passo 6: commit e PR 1.**

```bash
git add web/scripts web/vite.config.ts web/tsconfig.node.json
git commit -m "Web: preload da fonte do título, conferido pelo orçamento

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
git push -u origin HEAD:design-tokens
gh pr create --base main --head design-tokens --title "Web: tokens da revisão de design (pátina, elevação, z-index, tema sem script)" --body "Implementa as tarefas 1 a 6 do plano docs/superpowers/plans/2026-10-15-revisao-design-livros.md (spec docs/superpowers/specs/2026-10-15-revisao-design-livros-design.md).

🤖 Generated with [Claude Code](https://claude.com/claude-code)"
```

Inclua no PR os dois arquivos de `docs/superpowers/` desta branch. Espere os checks serem registrados e ficarem verdes antes de pedir o merge.

---

### Tarefa 7: confirmação para remover um arquivo

**Arquivos:**
- Modificar:
  - `web/src/text.ts` (`workbench`);
  - `web/src/workbench/Workbench.tsx` (estado `pendingRemoval`, `ConfirmDialog`);
  - `web/src/workbench/Workbench.test.tsx`.

**Interfaces:**
- Consome: `ConfirmDialog` (`open`, `title`, `children`, `confirmLabel`, `cancelLabel`, `onConfirm`, `onCancel`, `destructive`) e `batch.remove(localId: string): void`.
- Produz o tipo local `PendingRemoval = { kind: "one"; localId: string; name: string } | { kind: "all"; count: number }`, usado na Tarefa 8, e os textos `TEXT.workbench.removal.*`.

- [ ] **Passo 1: escrever os testes que falham.** Em `Workbench.test.tsx`, troque o teste "arquivo que falhou pode ser enviado de novo ou removido". A partir do clique em "Remover a.csv", ele passa a ser:

```ts
    await user.click(screen.getByRole("button", { name: "Remover a.csv" }));
    const dialog = screen.getByRole("dialog", { name: "Remover a.csv?" });
    expect(within(dialog).getByRole("button", { name: "Cancelar" })).toHaveFocus();
    await user.click(within(dialog).getByRole("button", { name: "Remover" }));

    expect(screen.getByRole("list", { name: "Ordem de processamento" })).not.toHaveTextContent(
      "a.csv",
    );
    expect(screen.getByLabelText("Escolher arquivos")).toHaveFocus();
  });

  it("cancelar a remoção mantém o arquivo", async () => {
    const { user } = await setup();
    await chooseFiles(user, ["a.csv"]);
    await waitUntilSent(1);

    await user.click(screen.getByRole("button", { name: "Remover a.csv" }));
    await user.click(screen.getByRole("button", { name: "Cancelar" }));

    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByRole("list", { name: "Ordem de processamento" })).toHaveTextContent("a.csv");
    expect(screen.getByRole("button", { name: "Remover a.csv" })).toHaveFocus();
  });

  it("remover durante o envio aborta o envio sem deixar erro", async () => {
    const { user, pending } = await setup({ autoResolve: false });
    await chooseFiles(user, ["a.csv"]);
    await waitFor(() => {
      expect(pending).toHaveLength(1);
    });

    await user.click(screen.getByRole("button", { name: "Remover a.csv" }));
    await user.click(screen.getByRole("button", { name: "Remover" }));

    expect(screen.queryByText("a.csv")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("confirmar duas vezes remove só o arquivo escolhido", async () => {
    const { user } = await setup();
    await chooseFiles(user, ["a.csv", "b.csv"]);
    await waitUntilSent(2);

    await user.click(screen.getByRole("button", { name: "Remover a.csv" }));
    const confirm = screen.getByRole("button", { name: "Remover" });
    await user.dblClick(confirm);

    const list = screen.getByRole("list", { name: "Ordem de processamento" });
    expect(list).not.toHaveTextContent("a.csv");
    expect(list).toHaveTextContent("b.csv");
  });
```

Se `chooseFiles`, `waitUntilSent` e `setup` tiverem outra assinatura, use as que já existem no arquivo. Os três já aparecem nos testes atuais.

Se o jsdom do projeto não implementar `HTMLDialogElement.showModal`, o `ConfirmDialog.test.tsx` mostra como ele é stubado. Use o mesmo setup, que deve estar em `src/test/setup.ts`.

- [ ] **Passo 2: rodar e ver falhar.**

Rode: `npm test -- src/workbench/Workbench.test.tsx`
Esperado: FAIL em "Unable to find role dialog", nos quatro testes.

- [ ] **Passo 3: textos.** Em `text.ts`, dentro de `workbench`, ao lado de `upload`, acrescente:

```ts
    removal: {
      titleOne: (name: string) => `Remover ${name}?`,
      bodyOne: "O arquivo sai do lote. Para usá-lo de novo, escolha-o outra vez.",
      titleAll: (count: number) => `Remover os ${String(count)} arquivos?`,
      bodyAll: "Todos os arquivos saem do lote. Para usá-los de novo, escolha-os outra vez.",
      confirm: "Remover",
      confirmAll: "Remover todos",
      cancel: "Cancelar",
      removeAll: "Remover todos",
    },
```

- [ ] **Passo 4: implementar.** Em `Workbench.tsx`:

1. Acrescente `import { ConfirmDialog } from "../components/ui/ConfirmDialog";`.
2. Logo abaixo dos imports, declare:

```ts
type PendingRemoval =
  | { readonly kind: "one"; readonly localId: string; readonly name: string }
  | { readonly kind: "all"; readonly count: number };
```

3. Dentro de `Workbench`, depois de `const [attempt, setAttempt] = useState(0);`, acrescente:

```ts
  const [pendingRemoval, setPendingRemoval] = useState<PendingRemoval | null>(null);
```

4. Troque o `removeEntry` atual por:

```ts
  // Remover pede confirmação (decisão do usuário, 2026-10-15). O alvo é o localId guardado ao
  // abrir, nunca a posição: confirmar duas vezes não alcança outro arquivo.
  const askRemoval = (localId: string): void => {
    const entry = batch.entries.find((candidate) => candidate.localId === localId);
    if (entry !== undefined) {
      setPendingRemoval({ kind: "one", localId, name: entry.file.name });
    }
  };

  const confirmRemoval = (): void => {
    if (pendingRemoval === null) {
      return;
    }
    if (pendingRemoval.kind === "one") {
      batch.remove(pendingRemoval.localId);
    } else {
      batch.removeAll();
    }
    setPendingRemoval(null);
    // O botão Remover some com o item: o foco volta à escolha de arquivos, e não ao body.
    document.getElementById(FILES_FIELD_ID)?.focus();
  };
```

   `batch.removeAll` nasce na Tarefa 8. Nesta tarefa, use `entries.forEach((entry) => { batch.remove(entry.localId); });` no ramo `"all"` e troque pela chamada na Tarefa 8.

5. No `UploadEntryView`, troque `onRemove={removeEntry}` por `onRemove={askRemoval}`.
6. Antes do `</form>`, depois do último `LiveRegion`, acrescente:

```tsx
        <ConfirmDialog
          open={pendingRemoval !== null}
          title={removalTitle(pendingRemoval)}
          confirmLabel={
            pendingRemoval?.kind === "all"
              ? TEXT.workbench.removal.confirmAll
              : TEXT.workbench.removal.confirm
          }
          cancelLabel={TEXT.workbench.removal.cancel}
          onConfirm={confirmRemoval}
          onCancel={() => {
            setPendingRemoval(null);
          }}
        >
          <p>
            {pendingRemoval?.kind === "all"
              ? TEXT.workbench.removal.bodyAll
              : TEXT.workbench.removal.bodyOne}
          </p>
        </ConfirmDialog>
```

7. Perto de `limitsHint`, no fim do arquivo:

```ts
function removalTitle(removal: PendingRemoval | null): string {
  if (removal === null) {
    return "";
  }
  return removal.kind === "one"
    ? TEXT.workbench.removal.titleOne(removal.name)
    : TEXT.workbench.removal.titleAll(removal.count);
}
```

**Foco no Cancelar:** o `<dialog>` nativo devolve o foco a quem abriu. Ao cancelar, o foco volta ao botão "Remover a.csv", e isso o teste confere. Ao confirmar, o `confirmRemoval` move o foco para a escolha de arquivos.

- [ ] **Passo 5: rodar e ver passar.**

Rode: `npm test -- src/workbench/Workbench.test.tsx && npm run typecheck && npm run lint`
Esperado: PASS. O teste do axe deste arquivo também passa.

- [ ] **Passo 6: commit.**

```bash
git add web/src/text.ts web/src/workbench
git commit -m "Web: remover um arquivo da Bancada pede confirmação

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Tarefa 8: "Remover todos" e o e2e da remoção

**Arquivos:**
- Modificar:
  - `web/src/upload/useUploadBatch.ts` (o `removeAll` e a interface `UploadBatch`);
  - `web/src/upload/useUploadBatch.test.ts`;
  - `web/src/workbench/Workbench.tsx` (o botão e o ramo `"all"`);
  - `web/src/workbench/Workbench.test.tsx`;
  - `web/e2e/pages/BancadaPage.ts` (ações `removeFile` e `removeAll`).
- Criar: `web/e2e/specs/removal.spec.ts`

**Interfaces:**
- Consome: `PendingRemoval` e `confirmRemoval` da Tarefa 7.
- Produz:
  - `UploadBatch.removeAll(): void`, que aborta todos os envios e esvazia o lote;
  - `BancadaPage.removeFile(name: string): Promise<void>`;
  - `BancadaPage.removeAll(): Promise<void>`.

- [ ] **Passo 1: escrever os testes que falham.** Em `useUploadBatch.test.ts`, use o mesmo helper de render do hook que os testes de `remove` já usam no arquivo:

```ts
  it("removeAll aborta os envios em curso e esvazia o lote", async () => {
    const { result, pending } = renderBatch({ autoResolve: false });
    act(() => {
      result.current.add([csv("a.csv"), csv("b.csv")]);
    });
    await waitFor(() => {
      expect(pending).toHaveLength(2);
    });

    act(() => {
      result.current.removeAll();
    });

    expect(result.current.entries).toEqual([]);
    expect(pending.every((upload) => upload.signal.aborted)).toBe(true);
  });
```

Se o helper do arquivo tiver outro nome, ou não expuser `pending` com `signal`, siga o teste de `remove` que já existe e asserte da mesma forma que ele (abortado e fora da lista).

Em `Workbench.test.tsx`:

```ts
  it("com 2 arquivos ou mais, Remover todos pede confirmação com a contagem", async () => {
    const { user } = await setup();
    await chooseFiles(user, ["a.csv", "b.csv"]);
    await waitUntilSent(2);

    await user.click(screen.getByRole("button", { name: "Remover todos" }));
    const dialog = screen.getByRole("dialog", { name: "Remover os 2 arquivos?" });
    await user.click(within(dialog).getByRole("button", { name: "Remover todos" }));

    expect(screen.queryByRole("list", { name: "Ordem de processamento" })).toBeNull();
    expect(screen.getByLabelText("Escolher arquivos")).toHaveFocus();
  });

  it("com 1 arquivo, não há Remover todos", async () => {
    const { user } = await setup();
    await chooseFiles(user, ["a.csv", "b.csv"]);
    await waitUntilSent(2);
    await user.click(screen.getByRole("button", { name: "Remover a.csv" }));
    await user.click(screen.getByRole("button", { name: "Remover" }));

    expect(screen.queryByRole("button", { name: "Remover todos" })).toBeNull();
  });
```

Crie `web/e2e/specs/removal.spec.ts`:

```ts
// Remoção na Bancada (spec 2026-10-15, decisão 5): confirmação, Remover todos e a página atrás do
// diálogo sem rolar.
import { BancadaPage } from "../pages/BancadaPage";
import { CSV_FILES } from "../fixtures/files";
import { expect, test } from "../support/test";

test("remover um, cancelar e remover todos", async ({ page }) => {
  const bancada = new BancadaPage(page);
  await bancada.open("csv-janitor");
  await bancada.addFiles(CSV_FILES);

  await page.getByRole("button", { name: `Remover ${CSV_FILES[0].name}` }).click();
  await expect(page.locator("html")).toHaveCSS("overflow", "hidden");
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toBeHidden();
  await expect(page.locator("html")).not.toHaveCSS("overflow", "hidden");
  expect(await bancada.order()).toEqual(CSV_FILES.map((file) => file.name));

  await bancada.removeFile(CSV_FILES[0].name);
  expect(await bancada.order()).toEqual([CSV_FILES[1].name]);

  await bancada.addFiles([CSV_FILES[0]]);
  await bancada.removeAll();
  await expect(page.getByRole("list", { name: "Ordem de processamento" })).toBeHidden();
});
```

Confira o export real do `test`/`expect` no topo das outras specs, como `journeys.spec.ts`, e siga o mesmo import.

- [ ] **Passo 2: rodar e ver falhar.**

Rode: `npm test -- src/upload/useUploadBatch.test.ts src/workbench/Workbench.test.tsx`
Esperado: FAIL com "removeAll is not a function" e "Unable to find role button Remover todos".

- [ ] **Passo 3: implementar o hook.** Em `useUploadBatch.ts`:

1. Na interface `UploadBatch`, depois de `remove`:

```ts
  /** Tira todos os arquivos do lote, abortando os envios em curso. */
  readonly removeAll: () => void;
```

2. Depois do `remove`:

```ts
  const removeAll = useCallback(() => {
    for (const controller of controllers.current.values()) {
      controller.abort();
    }
    controllers.current.clear();
    setEntries([]);
  }, []);
```

3. Troque o retorno por `return { entries, add, retry, cancel, remove, removeAll };`.

- [ ] **Passo 4: implementar a Bancada.** Em `Workbench.tsx`:

1. No ramo `"all"` do `confirmRemoval`, troque o `forEach` provisório por `batch.removeAll();`.
2. Logo depois do `<ReorderableList … />`, ainda dentro do ramo que tem arquivos, acrescente:

```tsx
            {entries.length >= 2 && (
              <Button
                type="button"
                variant="tertiary"
                className="self-start"
                onClick={() => {
                  setPendingRemoval({ kind: "all", count: entries.length });
                }}
              >
                {TEXT.workbench.removal.removeAll}
              </Button>
            )}
```

O ramo vira um fragmento `<>…</>` com a lista e o botão. Antes, confira em `Button.tsx` os nomes reais das variantes e se ele aceita `className`. Se não houver `tertiary`, use a variante de link ou terciária que existir. Se não aceitar `className`, envolva o botão num `<div className="self-start">`.

A contagem fica guardada na abertura, como pede o Foco da revisão (item 3).

- [ ] **Passo 5: implementar o page object.** Em `BancadaPage.ts`:

```ts
  /** Remove um arquivo pelo nome, confirmando no diálogo. */
  async removeFile(name: string): Promise<void> {
    await this.#page.getByRole("button", { name: `Remover ${name}` }).click();
    const dialog = this.#page.getByRole("dialog", { name: `Remover ${name}?` });
    await dialog.getByRole("button", { name: "Remover", exact: true }).click();
    await expect(dialog).toBeHidden();
  }

  /** Remove todos os arquivos, confirmando no diálogo com a contagem. */
  async removeAll(): Promise<void> {
    await this.#page.getByRole("button", { name: "Remover todos" }).click();
    const dialog = this.#page.getByRole("dialog", { name: /^Remover os \d+ arquivos\?$/ });
    await dialog.getByRole("button", { name: "Remover todos" }).click();
    await expect(dialog).toBeHidden();
  }
```

- [ ] **Passo 6: rodar e ver passar.**

Rode: `npm test && npm run typecheck && npm run lint && npm run format:check && npm run knip`
Esperado: PASS.

Rode: `npm run build && npm run e2e -- e2e/specs/removal.spec.ts e2e/specs/journeys.spec.ts e2e/specs/focus.spec.ts`
Esperado: PASS.

- [ ] **Passo 7: commit e PR 2.**

```bash
git add web/src web/e2e
git commit -m "Web: Remover todos na Bancada, com confirmação e contagem

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
git push -u origin HEAD:design-interacao
gh pr create --base main --head design-interacao --title "Web: confirmação ao remover e Remover todos na Bancada" --body "Implementa as tarefas 7 e 8 do plano docs/superpowers/plans/2026-10-15-revisao-design-livros.md.

🤖 Generated with [Claude Code](https://claude.com/claude-code)"
```

Espere os checks serem registrados e ficarem verdes.
