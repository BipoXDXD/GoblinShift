/** Relógio fixo dos testes: 14/10/2026 12:00 UTC, o mesmo nas telas e no cliente da API. */
const TEST_NOW_MS = Date.parse("2026-10-14T12:00:00.000Z");

export const testNow = (): number => TEST_NOW_MS;
