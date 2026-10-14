import tokensCss from "../styles/tokens.css?raw";

/** Texto do `tokens.css`: o teste mede o arquivo que o build usa, não uma cópia. */
export function readTokensCss(): string {
  return tokensCss;
}

/** Tons crus (`--color-moss-700: oklch(...)`) do bloco `@theme static`. */
export function rawColors(css: string): ReadonlyMap<string, string> {
  const block = blockAfter(css, "@theme static {");
  const colors = new Map<string, string>();
  for (const [, name, value] of block.matchAll(/--color-([a-z0-9-]+):\s*(oklch\([^)]*\));/g)) {
    if (name !== undefined && value !== undefined) {
      colors.set(name, value);
    }
  }
  return colors;
}

/**
 * Cor de cada token semântico do bloco `selector` (`:root` ou `[data-theme="dark"]`), já resolvida
 * até o tom cru. O tema escuro herda do `:root` o que não redefine, como no navegador.
 */
export function semanticColors(css: string, selector: string): ReadonlyMap<string, string> {
  const raw = rawColors(css);
  const declarations = new Map([
    ...declarationsOf(blockAfter(css, ":root {")),
    ...(selector === ":root" ? [] : declarationsOf(blockAfter(css, `${selector} {`))),
  ]);
  const resolved = new Map<string, string>();
  for (const name of declarations.keys()) {
    const color = resolve(name, declarations, raw);
    if (color !== null) {
      resolved.set(name, color);
    }
  }
  return resolved;
}

const MAX_VAR_DEPTH = 8;

function resolve(
  name: string,
  declarations: ReadonlyMap<string, string>,
  raw: ReadonlyMap<string, string>,
): string | null {
  let value = declarations.get(name);
  for (let depth = 0; depth < MAX_VAR_DEPTH && value !== undefined; depth += 1) {
    const reference = /^var\(--([a-z0-9-]+)\)$/.exec(value)?.[1];
    if (reference === undefined) {
      return value.startsWith("oklch(") ? value : null;
    }
    value = reference.startsWith("color-")
      ? raw.get(reference.slice("color-".length))
      : declarations.get(reference);
  }
  return null;
}

function declarationsOf(block: string): Map<string, string> {
  const declarations = new Map<string, string>();
  for (const [, name, value] of block.matchAll(/--([a-z0-9-]+):\s*([^;]+);/g)) {
    if (name !== undefined && value !== undefined) {
      declarations.set(name, value.trim());
    }
  }
  return declarations;
}

/** Corpo do bloco que começa em `opening`; os blocos de token não têm chaves aninhadas. */
function blockAfter(css: string, opening: string): string {
  const start = css.indexOf(opening);
  if (start === -1) {
    throw new Error(`tokens.css não tem o bloco ${opening}`);
  }
  const bodyStart = start + opening.length;
  const end = css.indexOf("}", bodyStart);
  return css.slice(bodyStart, end);
}
