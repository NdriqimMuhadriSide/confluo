// Per-business module switch and settings, through the dashboard.
import { chromium } from "playwright";

import { WEB, check, log, ownerWithBusiness } from "./helpers.mjs";

const browser = await chromium.launch();
const { page } = await ownerWithBusiness(browser, "modules");
const nav = page.locator("header nav");

for (const item of ["Inbox", "Calendar", "Customers", "Knowledge base"]) {
  await nav.getByRole("link", { name: item }).waitFor();
}
log("CRM is on by default; its nav items are shown");

await nav.getByRole("link", { name: "Settings" }).click();
const crm = page.locator('[data-module="crm"]');
await crm.getByRole("button", { name: "Turn off" }).click();
await crm.getByRole("button", { name: "Turn on" }).waitFor();
check((await nav.getByRole("link", { name: "Inbox" }).count()) === 0, "Inbox still in nav");
const res = await page.goto(`${WEB}/inbox`);
check(res.status() === 404, `expected 404 for /inbox, got ${res.status()}`);
log("turning CRM off removes its nav and its pages return 404");

await page.goto(`${WEB}/settings/modules`);
await crm.getByRole("button", { name: "Turn on" }).click();
await nav.getByRole("link", { name: "Inbox" }).waitFor();
check((await page.goto(`${WEB}/inbox`)).status() === 200, "/inbox not back");
log("turning it back on restores both");

await page.goto(`${WEB}/settings/modules`);
await crm.locator("#cfg-booking_mode").selectOption("auto");
await crm.locator("#cfg-reminder_hours_before").fill("48");
await crm.getByRole("button", { name: "Save settings" }).click();
await page.getByRole("status").getByText("Saved.").waitFor();
check((await crm.locator("#cfg-booking_mode").inputValue()) === "auto", "booking mode not saved");
check((await crm.locator("#cfg-reminder_hours_before").inputValue()) === "48", "reminder not saved");
log("settings are saved");

// Bypass the browser's min/max to prove the server validates against the schema.
await crm.locator("#cfg-reminder_hours_before").evaluate((el) => {
  el.removeAttribute("max");
  el.value = "999";
});
await crm.getByRole("button", { name: "Save settings" }).click();
await page.getByRole("alert").getByText(/reminder_hours_before/).waitFor();
check((await crm.locator("#cfg-reminder_hours_before").inputValue()) === "48", "invalid value stored");
log("an out-of-range value is rejected by the schema and nothing is stored");

await page.goto(`${WEB}/settings/activity`);
await page.locator('[data-audit="update tenant_module"]').first().waitFor();
await page.locator('[data-audit="insert tenant"]').waitFor();
log("the changes appear in Settings → Activity (audit log)");

await browser.close();
console.log("\nALL PASSED (modules)");
