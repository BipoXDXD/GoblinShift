// Tags de título que decidem, no playwright.config.ts, em que projeto e modo um teste é coletado.
// Um caso que não pode rodar num projeto não é coletado nele, em vez de rodar e pular.

/** Usa a API falsa do modo local: o modo remoto não coleta o caso. */
export const FAKE_API_TAG = "@fake-api";
/** Só a largura e o toque do celular: o projeto desktop não coleta o caso. */
export const MOBILE_TAG = "@mobile";
/** Pinça nativa via CDP, só no Chromium: o projeto WebKit não coleta o caso. */
export const CDP_TAG = "@cdp";
/** Precisa da API real atrás da SPA (/docs, /openapi.json): o modo local, com a API falsa, não coleta. */
export const REAL_API_TAG = "@api-real";
/** Lê dados da conta: contra a VPS sem chave, o caso não é coletado. */
export const ACCOUNT_TAG = "@conta";
/** Tab e anel de foco: o projeto mobile não coleta o caso. */
export const KEYBOARD_TAG = "@keyboard";

/** A tag no título, sem outra letra ou hífen colado (`@conta` não pega `@contato`). */
export function titleTag(name: string): RegExp {
  return new RegExp(`${name}(?![\\w-])`);
}
