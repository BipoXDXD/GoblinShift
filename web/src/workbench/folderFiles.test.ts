import { afterEach, describe, expect, it } from "vitest";

import {
  collectEntries,
  folderEntries,
  isHiddenOrSystem,
  pickFromFolderInput,
  supportsFolderPicker,
} from "./folderFiles";
import {
  fakeDir,
  fakeFile,
  fakeItem,
  fakeSpecial,
  fakeUnreadable,
  numbered,
} from "../test/fakeEntries";

const file = (name: string, content = "x") => new File([content], name);

describe("folderEntries", () => {
  it("sem nenhuma pasta entre os itens, devolve null e o caminho dos arquivos soltos segue", () => {
    expect(folderEntries([fakeItem(fakeFile("a.png"))])).toBeNull();
    expect(folderEntries([])).toBeNull();
  });

  it("com uma pasta entre os itens, devolve todas as entradas, na ordem", () => {
    const dir = fakeDir("fotos", [[fakeFile("a.png")]]);
    const loose = fakeFile("solto.png");

    expect(folderEntries([fakeItem(dir), fakeItem(loose)])).toEqual([dir, loose]);
  });
});

describe("collectEntries", () => {
  it("soltar uma pasta não envia o item da pasta", async () => {
    const dir = fakeDir("fotos", [[fakeFile("a.png"), fakeFile("b.png")]]);

    const pick = await collectEntries([dir], 10);

    expect(pick.files.map((f) => f.name)).toEqual(["a.png", "b.png"]);
    expect(pick.overflow).toBe(false);
  });

  it("pasta com mais de 100 entradas lê todos os lotes do readEntries", async () => {
    const all = numbered(150);
    const dir = fakeDir("lote", [all.slice(0, 100), all.slice(100)]);

    const pick = await collectEntries([dir], 200);

    expect(pick.files).toHaveLength(150);
  });

  it("pasta acima do limite não envia nada e explica", async () => {
    const dir = fakeDir("grande", [numbered(101)]);

    const pick = await collectEntries([dir], 100);

    expect(pick).toMatchObject({ files: [], overflow: true });
  });

  it("para de ler assim que passa do limite: soltar a pasta pessoal não enumera o disco", async () => {
    const dir = fakeDir("casa", [numbered(100), numbered(100), numbered(100)]);

    await collectEntries([dir], 50);

    expect(dir.batchesRead()).toBe(1);
  });

  it("subpastas entram em profundidade, cada nível na ordem do nome", async () => {
    const dir = fakeDir("raiz", [
      [
        fakeDir("b", [[fakeFile("b2.txt"), fakeFile("b10.txt")]]),
        fakeFile("z.txt"),
        fakeDir("a", [[fakeDir("fundo", [[fakeFile("f.txt")]])]]),
      ],
    ]);

    const pick = await collectEntries([dir], 10);

    expect(pick.files.map((f) => f.name)).toEqual(["f.txt", "b2.txt", "b10.txt", "z.txt"]);
  });

  it("o teto conta os arquivos de todas as subpastas juntas", async () => {
    const dir = fakeDir("raiz", [
      [fakeDir("a", [numbered(3, "a")]), fakeDir("b", [numbered(3, "b")])],
    ]);

    expect((await collectEntries([dir], 5)).overflow).toBe(true);
    expect((await collectEntries([dir], 6)).files).toHaveLength(6);
  });

  it("arquivo vazio dentro da pasta vai junto, como qualquer arquivo", async () => {
    const dir = fakeDir("docs", [[fakeFile("vazio.txt", ""), fakeFile("cheio.txt")]]);

    const pick = await collectEntries([dir], 10);

    expect(pick.files.map((f) => [f.name, f.size])).toEqual([
      ["cheio.txt", 1],
      ["vazio.txt", 0],
    ]);
  });

  it("entrada que não é arquivo regular nem pasta fica de fora e é contada", async () => {
    const dir = fakeDir("misto", [
      [fakeFile("ok.txt"), fakeSpecial("fifo"), fakeUnreadable("travado.bin")],
    ]);

    const pick = await collectEntries([dir], 10);

    expect(pick.files.map((f) => f.name)).toEqual(["ok.txt"]);
    expect(pick.skippedUnreadable).toBe(2);
  });

  it("ignora ocultos e de sistema e conta, sem descer em pasta oculta", async () => {
    const git = fakeDir(".git", [numbered(5)]);
    const dir = fakeDir("fotos", [
      [
        fakeFile(".DS_Store"),
        fakeFile("Thumbs.db"),
        fakeFile("desktop.ini"),
        fakeFile("._x.png"),
        git,
        fakeFile("x.png"),
      ],
    ]);

    const pick = await collectEntries([dir], 10);

    expect(pick.files.map((f) => f.name)).toEqual(["x.png"]);
    expect(pick.skippedHidden).toBe(5);
    expect(git.batchesRead()).toBe(0);
  });

  it("a própria pasta solta, mesmo com nome oculto, é lida", async () => {
    const pick = await collectEntries([fakeDir(".fotos", [[fakeFile("a.png")]])], 10);

    expect(pick.files.map((f) => f.name)).toEqual(["a.png"]);
  });

  it("ocultos não contam para o teto", async () => {
    const dir = fakeDir("fotos", [[fakeFile(".DS_Store"), ...numbered(3)]]);

    expect((await collectEntries([dir], 3)).overflow).toBe(false);
  });

  it("pasta que não abre fica de fora e é contada", async () => {
    const dir = fakeDir("raiz", [[fakeDir("trancada", "erro"), fakeFile("a.txt")]]);

    const pick = await collectEntries([dir], 10);

    expect(pick.files.map((f) => f.name)).toEqual(["a.txt"]);
    expect(pick.skippedUnreadable).toBe(1);
  });
});

describe("pickFromFolderInput", () => {
  const inFolder = (path: string, content = "x") => {
    const name = path.split("/").at(-1) ?? path;
    const result = file(name, content);
    Object.defineProperty(result, "webkitRelativePath", { value: path });
    return result;
  };

  it("ignora ocultos e de sistema, inclusive dentro de pasta oculta, e conta", () => {
    const pick = pickFromFolderInput(
      [
        inFolder("fotos/.DS_Store"),
        inFolder("fotos/Thumbs.db"),
        inFolder("fotos/.git/config"),
        inFolder("fotos/x.png"),
      ],
      10,
    );

    expect(pick.files.map((f) => f.name)).toEqual(["x.png"]);
    expect(pick.skippedHidden).toBe(3);
  });

  it("acima do limite não envia nada", () => {
    const files = Array.from({ length: 6 }, (_, i) => inFolder(`fotos/sub/${String(i)}.png`));

    expect(pickFromFolderInput(files, 5)).toMatchObject({ files: [], overflow: true });
  });

  it("segue a ordem do caminho, com números na ordem natural", () => {
    const pick = pickFromFolderInput(
      [inFolder("p/b/1.txt"), inFolder("p/10.txt"), inFolder("p/2.txt"), inFolder("p/a/9.txt")],
      10,
    );

    expect(pick.files.map((f) => f.webkitRelativePath)).toEqual([
      "p/2.txt",
      "p/10.txt",
      "p/a/9.txt",
      "p/b/1.txt",
    ]);
  });
});

describe("isHiddenOrSystem", () => {
  it.each([".DS_Store", "._foto.png", ".git", "thumbs.db", "THUMBS.DB", "Desktop.ini"])(
    "%s é oculto ou de sistema",
    (name) => {
      expect(isHiddenOrSystem(name)).toBe(true);
    },
  );

  it.each(["foto.png", "meu.thumbs.db", "desktop.ini.txt", "a.b"])("%s não é", (name) => {
    expect(isHiddenOrSystem(name)).toBe(false);
  });
});

describe("supportsFolderPicker", () => {
  afterEach(() => {
    Reflect.deleteProperty(HTMLInputElement.prototype, "webkitdirectory");
  });

  it("segue o webkitdirectory do navegador", () => {
    expect(supportsFolderPicker()).toBe(false);
    Object.defineProperty(HTMLInputElement.prototype, "webkitdirectory", {
      configurable: true,
      value: false,
      writable: true,
    });
    expect(supportsFolderPicker()).toBe(true);
  });
});
