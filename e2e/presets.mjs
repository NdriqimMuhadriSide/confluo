// Presets and the demo tenant: a new business started from the garage preset in the
// Dutch dashboard, and the seeded demo salon (`make seed`) with realistic data.
import { chromium } from "playwright";

import { WEB, check, createConfirmedUser, log, signIn } from "./helpers.mjs";

const browser = await chromium.launch();
const email = `preset+${Date.now()}@example.com`;
await createConfirmedUser(email);
const page = await (await browser.newContext({ locale: "nl-BE" })).newPage();
await signIn(page, email);
await page.getByText("Stel je zaak in", { exact: true }).waitFor();
log("the dashboard opens in Dutch for a Dutch browser");

const garage = `Garage Peeters ${Date.now()}`;
await page.fill("#name", garage);
await page.getByRole("radio", { name: /Garage \/ autowerkplaats/ }).check();
await page.getByTestId("create-business").click();
await page.getByTestId("business-heading").getByText(garage).waitFor();

await page.goto(`${WEB}/settings/services`);
for (const name of ["Olieverversing", "Bandenwissel", "Keuringsvoorbereiding", "Diagnose"]) {
  await page.locator(`[data-service="${name}"]`).waitFor();
}
await page.locator('[data-service="Bandenwissel"]').getByText(/45 min/).waitFor();
await page.goto(`${WEB}/settings/fields`);
await page.locator('[data-field="licence_plate"]').getByText("Nummerplaat").waitFor();
await page.goto(`${WEB}/settings/team`);
await page.locator('[data-resource="Mecanicien 1"]').waitFor();
await page.locator('[data-resource="Brug 1"]').waitFor();
await page.goto(`${WEB}/knowledge`);
await page.locator('[data-kb-item="Openingsuren"] [data-status="draft"]').waitFor();
await page.locator('[data-kb-item="Kan ik wachten tijdens de herstelling?"]').waitFor();
log("garage preset: services, booking fields, staff and bay, Dutch knowledge drafts");

// The demo tenant (make seed).
const demo = await (await browser.newContext({ locale: "nl-BE" })).newPage();
await demo.goto(`${WEB}/login`);
await demo.fill("#email", "demo@confluo.local");
await demo.fill("#password", "demo-confluo-2026");
await demo.getByTestId("sign-in").click();
await demo.waitForURL(`${WEB}/`);
await demo.getByTestId("business-heading").getByText("Kapsalon Demo").waitFor();
await demo.goto(`${WEB}/settings/team`);
for (const name of ["Eva", "Lotte", "Sam"]) await demo.locator(`[data-resource="${name}"]`).waitFor();
await demo.locator('[data-resource="Eva"]').getByRole("link").click();
await demo.getByTestId("resource-name").getByText("Eva").waitFor();
await demo.getByTestId("free-time").locator("li").first().waitFor();
const free = await demo.getByTestId("free-time").locator("li").count();
check(free > 0, "demo stylist has no free time");
await demo.goto(`${WEB}/knowledge?q=${encodeURIComponent("Waar kan ik parkeren?")}`);
const hit = await demo.getByTestId("kb-hits").locator("li").first().getAttribute("data-hit");
check(hit === "Waar vind je ons", `unexpected top hit: ${hit}`);
log("demo salon: three stylists with schedules, free time, indexed knowledge base answers questions");

await browser.close();
console.log("\nALL PASSED (presets)");
