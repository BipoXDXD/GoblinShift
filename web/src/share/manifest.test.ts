import { describe, expect, it } from "vitest";
import { z } from "zod";

import manifestText from "../../public/manifest.webmanifest?raw";
import { PATHS } from "../app/paths";
import { RECIPE_LIST } from "../recipes/registry";
import { SHARE_FIELD } from "./inbox";

const manifestSchema = z.object({
  id: z.string(),
  scope: z.string(),
  display: z.string(),
  share_target: z.object({
    action: z.string(),
    method: z.string(),
    enctype: z.string(),
    params: z.object({
      files: z.array(z.object({ name: z.string(), accept: z.array(z.string()) })).length(1),
    }),
  }),
});

const manifest = manifestSchema.parse(JSON.parse(manifestText));

describe("manifest do PWA", () => {
  it("o accept do share_target é a união dos accept das receitas", () => {
    expect(new Set(manifest.share_target.params.files[0]?.accept)).toEqual(
      new Set(RECIPE_LIST.flatMap((recipe) => recipe.accept?.split(",") ?? [])),
    );
    expect(manifest.display).toBe("minimal-ui");
  });

  it("o share_target manda os arquivos por POST multipart para a rota que o service worker recebe", () => {
    expect(manifest.share_target).toMatchObject({
      action: PATHS.share,
      method: "POST",
      enctype: "multipart/form-data",
      params: { files: [{ name: SHARE_FIELD }] },
    });
    expect(manifest.id).toBe("/");
    expect(manifest.scope).toBe("/");
  });
});
