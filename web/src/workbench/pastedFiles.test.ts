import { describe, expect, it } from "vitest";

import { filesFromClipboard } from "./pastedFiles";

const clipboardWith = (...files: File[]) => ({ files });

describe("filesFromClipboard", () => {
  it("renomeia a imagem genérica colada e mantém o resto", () => {
    const data = clipboardWith(
      new File(["a"], "image.png", { type: "image/png" }),
      new File(["b"], "notas.csv"),
    );
    let n = 0;

    expect(filesFromClipboard(data, () => ++n).map((file) => file.name)).toEqual([
      "colado-1.png",
      "notas.csv",
    ]);
  });

  it("a imagem renomeada mantém o conteúdo e o tipo", async () => {
    const data = clipboardWith(new File(["pixels"], "IMAGE.JPEG", { type: "image/jpeg" }));

    const [file] = filesFromClipboard(data, () => 7);

    expect(file?.name).toBe("colado-7.jpeg");
    expect(file?.type).toBe("image/jpeg");
    expect(await file?.text()).toBe("pixels");
  });

  it("nome parecido, mas de verdade, não muda", () => {
    const data = clipboardWith(new File(["a"], "image-final.png"), new File(["b"], "image.svg"));

    expect(filesFromClipboard(data, () => 1).map((file) => file.name)).toEqual([
      "image-final.png",
      "image.svg",
    ]);
  });

  it("área de transferência ausente ou sem arquivos devolve nada", () => {
    expect(filesFromClipboard(null, () => 1)).toEqual([]);
    expect(filesFromClipboard(clipboardWith(), () => 1)).toEqual([]);
  });
});
