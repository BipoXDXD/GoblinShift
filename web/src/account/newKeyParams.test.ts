import { describe, expect, it } from "vitest";

import type { ApiProblem } from "../api/problem";
import { ApiError } from "../api/response";
import { SCOPES } from "./api";
import {
  isReauthRequired,
  newKeyChoicesFromSearch,
  newKeyChoicesToSearch,
  withoutNewKeyChoices,
} from "./newKeyParams";
import { safeReturnTo } from "./signIn";

describe("newKeyChoicesToSearch", () => {
  it("leva escopos repetidos e dias, na ordem de SCOPES, e preserva outros parâmetros", () => {
    const search = newKeyChoicesToSearch(
      { scopes: ["runs:write", "files:read"], days: "7" },
      new URLSearchParams("aba=x"),
    );

    expect(search.toString()).toBe(
      "aba=x&nova_chave=1&escopo=files%3Aread&escopo=runs%3Awrite&dias=7",
    );
  });

  it("troca as escolhas anteriores em vez de somar", () => {
    const search = newKeyChoicesToSearch(
      { scopes: ["runs:read"], days: "15" },
      new URLSearchParams("nova_chave=1&escopo=files%3Aread&dias=7"),
    );

    expect(search.toString()).toBe("nova_chave=1&escopo=runs%3Aread&dias=15");
  });

  it("não altera os parâmetros recebidos", () => {
    const current = new URLSearchParams("aba=x");

    newKeyChoicesToSearch({ scopes: ["runs:read"], days: "7" }, current);

    expect(current.toString()).toBe("aba=x");
  });

  it("o resultado passa no safeReturnTo com todos os escopos", () => {
    const search = newKeyChoicesToSearch(
      { scopes: [...SCOPES], days: "90" },
      new URLSearchParams(),
    );

    expect(safeReturnTo("/acesso", `?${search.toString()}`)).toBe(`/acesso?${search.toString()}`);
  });
});

describe("newKeyChoicesFromSearch", () => {
  it("volta as escolhas e descarta escopo desconhecido ou repetido", () => {
    expect(
      newKeyChoicesFromSearch(
        new URLSearchParams(
          "nova_chave=1&escopo=runs%3Aread&escopo=admin%3Aall&escopo=runs%3Aread&dias=15",
        ),
      ),
    ).toEqual({ scopes: ["runs:read"], days: "15" });
  });

  it("devolve os escopos na ordem de SCOPES", () => {
    expect(
      newKeyChoicesFromSearch(
        new URLSearchParams("nova_chave=1&escopo=runs%3Awrite&escopo=files%3Aread&dias=7"),
      )?.scopes,
    ).toEqual(["files:read", "runs:write"]);
  });

  it.each(["0", "91", "abc", "", "1e2", "007", "07", " 7", "-1"])(
    "dias %j fora de KEY_DAYS viram o inicial",
    (dias) => {
      expect(newKeyChoicesFromSearch(new URLSearchParams({ nova_chave: "1", dias }))?.days).toBe(
        "30",
      );
    },
  );

  it("sem o parâmetro de dias, o inicial", () => {
    expect(newKeyChoicesFromSearch(new URLSearchParams("nova_chave=1"))).toEqual({
      scopes: [],
      days: "30",
    });
  });

  it.each(["1", "90"])("dias %j no limite valem", (dias) => {
    expect(newKeyChoicesFromSearch(new URLSearchParams({ nova_chave: "1", dias }))?.days).toBe(
      dias,
    );
  });

  it.each(["escopo=runs%3Aread", "nova_chave=0&escopo=runs%3Aread", "nova_chave=sim"])(
    "sem nova_chave=1 não há escolhas (%s)",
    (query) => {
      expect(newKeyChoicesFromSearch(new URLSearchParams(query))).toBeNull();
    },
  );
});

describe("withoutNewKeyChoices", () => {
  it("tira só os três parâmetros", () => {
    expect(
      withoutNewKeyChoices(
        new URLSearchParams("aba=x&nova_chave=1&escopo=runs%3Aread&dias=7"),
      ).toString(),
    ).toBe("aba=x");
  });
});

describe("isReauthRequired", () => {
  const problem = (code: ApiProblem["code"]): ApiError =>
    new ApiError({
      kind: "problem",
      status: 401,
      retryAfterMs: null,
      problem: {
        type: "x",
        title: "x",
        status: 401,
        detail: "x",
        instance: "/x",
        code,
        request_id: "r",
      },
    });

  it("reconhece o REAUTH_REQUIRED", () => {
    expect(isReauthRequired(problem("REAUTH_REQUIRED"))).toBe(true);
  });

  it("ignora outros códigos e erros comuns", () => {
    expect(isReauthRequired(problem("STATE_CONFLICT"))).toBe(false);
    expect(isReauthRequired(new Error("REAUTH_REQUIRED"))).toBe(false);
  });
});
