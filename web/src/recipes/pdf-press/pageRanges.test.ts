import { describe, expect, it } from "vitest";

import { TEXT } from "../../text";
import { parsePageRanges } from "./pageRanges";

const T = TEXT.pdfPress.ranges;

describe("parsePageRanges", () => {
  it.each([
    [
      "1-3, 7, 10-",
      [
        { first: 1, last: 3 },
        { first: 7, last: 7 },
        { first: 10, last: null },
      ],
    ],
    [
      " 2 ,2,1–2 ",
      [
        { first: 2, last: 2 },
        { first: 2, last: 2 },
        { first: 1, last: 2 },
      ],
    ],
    ["3 - 5", [{ first: 3, last: 5 }]],
    ["200", [{ first: 200, last: 200 }]],
  ])("lê %j", (text, ranges) => {
    expect(parsePageRanges(text, 200)).toEqual({ ok: true, ranges });
  });

  it.each([
    ["", T.empty],
    ["   ", T.empty],
    ["1,,3", T.invalidPart("")],
    ["1,", T.invalidPart("")],
    ["-3", T.invalidPart("-3")],
    ["a", T.invalidPart("a")],
    ["n", T.invalidPart("n")],
    ["2n-1", T.invalidPart("2n-1")],
    ["1.5", T.invalidPart("1.5")],
    ["0", T.startsAtOne],
    ["0-3", T.startsAtOne],
    ["201", T.beyondLimit(201, 200)],
    ["1-201", T.beyondLimit(201, 200)],
    ["5-2", T.reversed("5-2")],
    ["1-3-5", T.invalidPart("1-3-5")],
  ])("recusa %j com mensagem clara", (text, message) => {
    expect(parsePageRanges(text, 200)).toEqual({ ok: false, message });
  });

  it("aponta a primeira parte com problema, na ordem do texto", () => {
    expect(parsePageRanges("1, 9-4, x", 200)).toEqual({ ok: false, message: T.reversed("9-4") });
  });
});
