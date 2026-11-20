// Mozilla 2090055 and microsoft/playwright#43982, using public synthetic content only.
import assert from "node:assert/strict";

import AxeBuilder from "@axe-core/playwright";
import { expect, firefox } from "@playwright/test";

const negative = process.argv.includes("--expect-crash");
const executablePath = negative ? undefined : process.env["GS_FIREFOX_EXECUTABLE"];
assert(negative || executablePath, "The positive control requires verified Firefox r1575");

const html =
  '<!doctype html><html lang="en"><head><title>ARIA regression</title></head>' +
  '<body><main><section aria-labelledby="title"><h1 id="title">Sign in</h1>' +
  '<label for="input">Password</label><input id="input"></section></main></body></html>';

for (const trigger of ["reflection", "axe"] as const) {
  const browser = await firefox.launch(executablePath ? { executablePath } : {});
  try {
    const page = await browser.newPage();
    await page.setContent(html);
    if (trigger === "axe") {
      expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
    } else {
      await page.evaluate("document.querySelector('section').ariaLabelledByElements");
    }
    const lookup = () => page.getByLabel("Password").count();
    if (negative) {
      await assert.rejects(lookup, /Page crashed/);
      console.log(`Firefox r1572 negative control: ${trigger} reproduced Page crashed`);
    } else {
      assert.equal(await lookup(), 1);
      await expect(page.getByRole("region", { name: "Sign in" })).toBeVisible();
      await page.getByLabel("Password").fill("synthetic");
      await expect(page.getByLabel("Password")).toHaveValue("synthetic");
      console.log(`Firefox r1575 positive control: ${trigger} passed`);
    }
  } finally {
    await browser.close();
  }
}
