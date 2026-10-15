import { describe, expect, it } from "vitest";

import { parseNoticeFile } from "./notice";
import noticeFile from "./notice.json";

describe("notice.json do repositório", () => {
  // Publicar um aviso é um PR que edita o arquivo: um aviso malformado para aqui, antes do merge.
  it("é um aviso válido ou nenhum", () => {
    expect(() => parseNoticeFile(noticeFile)).not.toThrow();
  });
});
