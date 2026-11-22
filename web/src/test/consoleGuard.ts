// Guard de console dos testes do Vitest. O setup.ts liga o guard antes de cada teste e, no fim,
// reprova o teste com os console.warn/console.error que nenhum `allowConsole` liberou e as liberações
// que não casaram com nada. log/info/debug são gravados e repassados, mas nunca são violação.
//
// Os gravadores entram por atribuição direta, sem vi.spyOn: assim o `restoreMocks` do Vitest e o
// espião que um teste instala em console.error não tiram o guard do lugar no teste seguinte.

export type ConsoleMethod = "log" | "info" | "debug" | "warn" | "error";
export type GuardedMethod = Extract<ConsoleMethod, "warn" | "error">;
type ConsoleFn = (...args: unknown[]) => void;

export interface ConsoleCall {
  readonly method: ConsoleMethod;
  readonly args: readonly unknown[];
}

interface Allowance {
  readonly method: GuardedMethod;
  readonly pattern: RegExp;
  readonly reason: string;
  used: boolean;
}

const METHODS = [
  "log",
  "info",
  "debug",
  "warn",
  "error",
] as const satisfies readonly ConsoleMethod[];
const MAX_MESSAGE_LENGTH = 300;
const FORMAT_TOKEN = /%[sdifoOc%]/g;

// Capturados uma vez, no carregamento: é para eles que o guard repassa e é a eles que restaura.
const ORIGINAL: Readonly<Record<ConsoleMethod, ConsoleFn>> = {
  log: console.log,
  info: console.info,
  debug: console.debug,
  warn: console.warn,
  error: console.error,
};

let calls: ConsoleCall[] = [];
let unallowed: { readonly method: GuardedMethod; readonly message: string }[] = [];
let allowances: Allowance[] = [];
// O estado acima é um só no módulo: dois testes ao mesmo tempo (it.concurrent) ou um setup duplicado
// misturariam as chamadas. `active` deixa o start perceber que o stop anterior não rodou.
let active = false;

function isGuarded(method: ConsoleMethod): method is GuardedMethod {
  return method === "warn" || method === "error";
}

function formatArg(arg: unknown): string {
  // O DOMException do jsdom não herda de Error e sairia do JSON como "{}".
  if (arg instanceof Error || arg instanceof DOMException) {
    return `${arg.name}: ${arg.message}`;
  }
  if (typeof arg === "object" && arg !== null) {
    try {
      return JSON.stringify(arg);
    } catch {
      return formatUnserializable(arg);
    }
  }
  return String(arg);
}

// Objeto que o JSON não serializa (circular, BigInt): vale o toString próprio, se houver, e
// "[object Object]" basta para o relatório. Sem protótipo, String() lança; aí vale o toString de
// Object.prototype, que não depende do objeto.
function formatUnserializable(arg: object): string {
  try {
    // eslint-disable-next-line @typescript-eslint/no-base-to-string -- fallback deliberado, ver acima
    return String(arg);
  } catch {
    // O `{}` empresta o toString de Object.prototype (tipado como any) sem perder o tipo.
    return {}.toString.call(arg);
  }
}

function formatConsoleArgs(args: readonly unknown[]): string {
  const [first, ...rest] = args;
  if (typeof first !== "string") {
    return args.map(formatArg).join(" ");
  }
  // Como no util.format: a string sozinha sai como está, e com argumentos o %% vira % sem consumir
  // nenhum.
  if (rest.length === 0) {
    return first;
  }
  const remaining = [...rest];
  const head = first.replace(FORMAT_TOKEN, (token) => {
    if (token === "%%") {
      return "%";
    }
    if (remaining.length === 0) {
      return token;
    }
    const value = remaining.shift();
    return token === "%c" ? "" : formatArg(value);
  });
  return [head, ...remaining.map(formatArg)].join(" ");
}

function isAllowed(method: GuardedMethod, message: string): boolean {
  let allowed = false;
  for (const allowance of allowances) {
    if (allowance.method === method && message.search(allowance.pattern) !== -1) {
      allowance.used = true;
      allowed = true;
    }
  }
  return allowed;
}

function record(method: ConsoleMethod, args: readonly unknown[]): void {
  calls.push({ method, args });
  if (isGuarded(method)) {
    const message = formatConsoleArgs(args);
    if (isAllowed(method, message)) {
      return;
    }
    unallowed.push({ method, message });
  }
  ORIGINAL[method].apply(console, [...args]);
}

// Um gravador fixo por método, criado uma vez: se um `restoreMocks` atrasado devolver ao console
// um gravador de outro teste, ele ainda grava no estado corrente.
const RECORDERS: Readonly<Record<ConsoleMethod, ConsoleFn>> = {
  log: (...args) => {
    record("log", args);
  },
  info: (...args) => {
    record("info", args);
  },
  debug: (...args) => {
    record("debug", args);
  },
  warn: (...args) => {
    record("warn", args);
  },
  error: (...args) => {
    record("error", args);
  },
};

export function allowConsole(method: GuardedMethod, pattern: RegExp, reason: string): void {
  if (reason.trim() === "") {
    throw new Error("allowConsole precisa de um motivo não vazio");
  }
  // Com a flag y a regex só casa no índice 0 da mensagem, o que surpreende quem lê a liberação.
  if (pattern.sticky) {
    throw new Error(`allowConsole não aceita regex sticky (flag y): ${String(pattern)}`);
  }
  allowances.push({ method, pattern, reason, used: false });
}

export function consoleCalls(): readonly ConsoleCall[] {
  return calls;
}

// Com o guard ainda ativo (o stop anterior não rodou), avisa e recomeça do zero em vez de lançar: um
// afterEach de arquivo que lança pula o afterEach do setup, e lançar aqui reprovaria em cascata o
// teste seguinte, que não tem culpa. `warn` existe para o teste do guard ler o aviso.
export function startConsoleGuard(warn: (message: string) => void = ORIGINAL.warn): void {
  if (active) {
    warn(
      "[console-guard] O guard de console já estava ativo: o stop do teste anterior não rodou " +
        "(afterEach que lançou?) ou dois testes dividem o guard (it.concurrent?). Recomeça do zero.",
    );
  }
  active = true;
  calls = [];
  unallowed = [];
  allowances = [];
  for (const method of METHODS) {
    console[method] = RECORDERS[method];
  }
}

export function stopConsoleGuard(): readonly string[] {
  active = false;
  for (const method of METHODS) {
    console[method] = ORIGINAL[method];
  }
  const unallowedLines = unallowed.map(
    ({ method, message }) =>
      `console.${method} sem liberação: ${message.slice(0, MAX_MESSAGE_LENGTH)}`,
  );
  const unusedLines = allowances
    .filter((allowance) => !allowance.used)
    .map(
      ({ method, pattern, reason }) =>
        `liberação sem uso: console.${method} ${String(pattern)} (${reason})`,
    );
  return [...unallowedLines, ...unusedLines];
}
