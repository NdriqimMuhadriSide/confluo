// Knowledge base in the dashboard: write an item, publish it, wait for the worker to
// index it, then find it with a natural question; unpublishing removes it from search.
import { chromium } from "playwright";

import { WEB, check, log, ownerWithBusiness } from "./helpers.mjs";

const browser = await chromium.launch();
const { page } = await ownerWithBusiness(browser, "kb");
await page.locator("aside nav").getByRole("link", { name: "Knowledge base" }).click();

async function add(title, body) {
  const form = page.locator('[data-kb-form="new"]');
  await form.getByLabel("Title").fill(title);
  await form.getByLabel("Text").fill(body);
  await page.getByTestId("create-kb").click();
  await page.locator(`[data-kb-item="${title}"] [data-status="draft"]`).waitFor();
}
await add("Parking", "Free parking behind the salon, entrance via the Kouter.");
await add("Payment", "We accept Bancontact, Visa, Mastercard and cash.");
log("items are saved as drafts");

for (const title of ["Parking", "Payment"]) {
  await page.locator(`[data-kb-item="${title}"]`).getByTestId("publish").click();
  await page.getByRole("status").getByText("Saved.").waitFor();
}
for (const title of ["Parking", "Payment"]) {
  let live = false;
  for (let i = 0; i < 30 && !live; i++) {
    await page.reload();
    live = (await page.locator(`[data-kb-item="${title}"] [data-status="live"]`).count()) > 0;
    if (!live) await page.waitForTimeout(500);
  }
  check(live, `${title} never became live`);
}
log("publishing indexes the items in the background (draft → indexing → live)");

await page.getByRole("searchbox").fill("Is there free parking?");
await page.getByTestId("kb-search").click();
const first = page.getByTestId("kb-hits").locator("li").first();
check((await first.getAttribute("data-hit")) === "Parking", "Parking should be the top hit");
log("a natural question finds the right item (hybrid search)");

await page.goto(`${WEB}/knowledge`);
await page.locator('[data-kb-item="Parking"]').getByTestId("unpublish").click();
await page.locator('[data-kb-item="Parking"] [data-status="draft"]').waitFor();
await page.getByRole("searchbox").fill("free parking");
await page.getByTestId("kb-search").click();
await page.waitForLoadState("networkidle");
check((await page.locator('[data-hit="Parking"]').count()) === 0, "unpublished item still found");
log("unpublished items drop out of search");

await browser.close();
console.log("\nALL PASSED (knowledge)");
