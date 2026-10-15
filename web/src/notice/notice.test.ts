import { describe, expect, it } from "vitest";

import {
  isNoticeActive,
  parseNoticeFile,
  readDismissedNoticeId,
  writeDismissedNoticeId,
  type SiteNotice,
} from "./notice";

const VALID = {
  id: "termos-v2",
  kind: "terms-change",
  title: "Os termos vão mudar",
  body: "A versão 2 vale a partir de 1º de novembro.",
  link: { href: "/termos", label: "Ler os termos" },
  from: "2026-10-15T00:00:00Z",
  until: "2026-11-01T00:00:00-03:00",
};

function fileWith(overrides: Record<string, unknown>): unknown {
  return { notice: { ...VALID, ...overrides } };
}

function without(key: keyof typeof VALID): unknown {
  return { notice: Object.fromEntries(Object.entries(VALID).filter(([name]) => name !== key)) };
}

describe("parseNoticeFile", () => {
  it("sem aviso, devolve null", () => {
    expect(parseNoticeFile({ notice: null })).toBeNull();
  });

  it("converte o aviso válido, com as datas em ms", () => {
    expect(parseNoticeFile({ notice: VALID })).toEqual({
      id: "termos-v2",
      kind: "terms-change",
      title: "Os termos vão mudar",
      body: "A versão 2 vale a partir de 1º de novembro.",
      link: { href: "/termos", label: "Ler os termos" },
      fromMs: Date.parse("2026-10-15T00:00:00Z"),
      untilMs: Date.parse("2026-11-01T03:00:00Z"),
    });
  });

  it("sem until nem link, o aviso fica aberto e sem link", () => {
    const notice = parseNoticeFile({
      notice: { ...VALID, link: undefined, until: undefined },
    });

    expect(notice?.untilMs).toBeNull();
    expect(notice?.link).toBeNull();
  });

  it.each([
    ["arquivo sem a chave notice", {}],
    ["arquivo que não é objeto", "aviso"],
    ["campo extra no arquivo", { notice: null, outro: 1 }],
    ["campo extra no aviso", fileWith({ priority: 1 })],
    ["tipo desconhecido", fileWith({ kind: "maintenance" })],
    ["id com maiúscula", fileWith({ id: "Termos" })],
    ["id vazio", fileWith({ id: "" })],
    ["id com 65 caracteres", fileWith({ id: "a".repeat(65) })],
    ["título vazio", fileWith({ title: "" })],
    ["título com 121 caracteres", fileWith({ title: "a".repeat(121) })],
    ["corpo com 601 caracteres", fileWith({ body: "a".repeat(601) })],
    ["NUL no corpo", fileWith({ body: "a\u0000b" })],
    ["quebra de linha no título", fileWith({ title: "a\nb" })],
    ["data sem fuso", fileWith({ from: "2026-10-15T00:00:00" })],
    ["data que não é data", fileWith({ from: "amanhã" })],
    ["until igual a from", fileWith({ until: VALID.from })],
    ["until antes de from", fileWith({ until: "2026-10-14T23:59:59Z" })],
    ["link externo", fileWith({ link: { href: "https://exemplo.com", label: "x" } })],
    ["link de protocolo relativo", fileWith({ link: { href: "//exemplo.com", label: "x" } })],
    ["link javascript:", fileWith({ link: { href: "javascript:alert(1)", label: "x" } })],
    ["link sem rótulo", fileWith({ link: { href: "/termos", label: "" } })],
    ["sem id", without("id")],
    ["sem from", without("from")],
    ["sem corpo", without("body")],
  ])("recusa %s", (_case, file) => {
    expect(() => parseNoticeFile(file)).toThrow("Aviso inválido em notice.json");
  });

  it.each([
    ["id com 64 caracteres", { id: "a".repeat(64) }],
    ["título com 120 caracteres", { title: "a".repeat(120) }],
    ["corpo com 600 caracteres", { body: "a".repeat(600) }],
  ])("aceita %s", (_case, overrides) => {
    expect(parseNoticeFile(fileWith(overrides))).not.toBeNull();
  });
});

describe("isNoticeActive", () => {
  const FROM = Date.parse("2026-10-15T00:00:00Z");
  const UNTIL = Date.parse("2026-11-01T00:00:00Z");
  const notice: SiteNotice = {
    id: "x",
    kind: "incident",
    title: "t",
    body: "b",
    link: null,
    fromMs: FROM,
    untilMs: UNTIL,
  };

  it.each([
    ["1 ms antes de from", FROM - 1, false],
    ["em from", FROM, true],
    ["1 ms antes de until", UNTIL - 1, true],
    ["em until", UNTIL, false],
    ["depois de until", UNTIL + 1, false],
  ])("%s → %s", (_case, now, expected) => {
    expect(isNoticeActive(notice, now)).toBe(expected);
  });

  it("sem until, continua ativo depois de from", () => {
    expect(isNoticeActive({ ...notice, untilMs: null }, UNTIL * 2)).toBe(true);
  });
});

describe("aviso fechado no armazenamento", () => {
  const throwing = (): Storage => {
    throw new DOMException("bloqueado", "SecurityError");
  };
  const failingStorage = (): Pick<Storage, "getItem" | "setItem"> => ({
    getItem: () => {
      throw new DOMException("bloqueado", "SecurityError");
    },
    setItem: () => {
      throw new DOMException("cheio", "QuotaExceededError");
    },
  });

  it("grava e lê o id fechado", () => {
    writeDismissedNoticeId(() => window.localStorage, "termos-v2");

    expect(readDismissedNoticeId(() => window.localStorage)).toBe("termos-v2");
  });

  it("nada gravado → null", () => {
    expect(readDismissedNoticeId(() => window.localStorage)).toBeNull();
  });

  it.each([
    ["o acesso ao localStorage lança", throwing],
    ["getItem e setItem lançam", failingStorage],
  ])("quando %s, ler devolve null e gravar não lança", (_case, storage) => {
    expect(readDismissedNoticeId(storage)).toBeNull();
    expect(() => {
      writeDismissedNoticeId(storage, "termos-v2");
    }).not.toThrow();
  });
});
