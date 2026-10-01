// Business setup through the dashboard: location with opening hours, a booking field,
// a staff member with a weekly schedule and a day off, and a service that requires
// the field. The free-time preview must reflect the schedule and the day off.
import { chromium } from "playwright";

import { WEB, check, log, ownerWithBusiness } from "./helpers.mjs";

const browser = await chromium.launch();
const { page } = await ownerWithBusiness(browser, "setup");

// Location with opening hours (all days start closed).
await page.goto(`${WEB}/settings/locations`);
const newLoc = page.locator('[data-location-form="new"]');
await newLoc.getByLabel("Name").fill("Gent centrum");
await newLoc.getByLabel("Address").fill("Veldstraat 12, 9000 Gent");
for (const day of ["mon", "tue", "wed", "thu", "fri"]) {
  await newLoc.locator(`[data-day="${day}"] input[type=checkbox]`).uncheck();
}
await newLoc.getByRole("button", { name: "Add" }).click();
await page.getByRole("status").getByText("Saved.").waitFor();
await page.locator('[data-location-form="Gent centrum"]').waitFor();
check(
  (await page.locator('[data-location-form="Gent centrum"] [data-day="sat"] input[type=checkbox]').isChecked()),
  "Saturday should be closed",
);
log("location with opening hours created");

// Booking field.
await page.goto(`${WEB}/settings/fields`);
await page.getByLabel("Label").fill("Licence plate");
await page.getByLabel("Type", { exact: true }).selectOption("text");
await page.getByTestId("add-field").click();
await page.locator('[data-field="licence_plate"]').waitFor();
log("booking field created");

// Staff member with a weekly schedule, lunch break on Monday, and a day off.
await page.goto(`${WEB}/settings/team`);
await page.getByLabel("Name").fill("Eva");
await page.getByLabel("Location").selectOption({ label: "Gent centrum" });
await page.getByTestId("add-resource").click();
await page.locator('[data-resource="Eva"]').getByRole("link").click();
await page.getByTestId("resource-name").getByText("Eva").waitFor();
for (const day of ["mon", "tue", "wed", "thu", "fri"]) {
  await page.locator(`[data-day="${day}"] input[type=checkbox]`).uncheck();
}
await page.getByLabel("Monday Break from").fill("12:30");
await page.getByLabel("Monday Break until").fill("13:30");
await page.getByTestId("save-schedule").click();
await page.getByRole("status").getByText("Saved.").waitFor();

const today = new Date();
const daysToMonday = ((8 - today.getDay()) % 7) || 7; // next Monday, never today
const monday = new Date(today.getFullYear(), today.getMonth(), today.getDate() + daysToMonday);
const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const tuesday = new Date(monday.getFullYear(), monday.getMonth(), monday.getDate() + 1);
await page.getByLabel("First day").fill(iso(tuesday));
await page.getByLabel("Reason").fill("Dentist");
await page.getByTestId("add-day-off").click();
await page.locator('[data-day-off="Dentist"]').waitFor();
log("staff member with weekly schedule (lunch break) and a day off");

// Free time: only check if next Monday/Tuesday fall within the 7-day preview.
if (daysToMonday <= 5) {
  const spans = await page.getByTestId("free-time").locator("li").evaluateAll((els) => els.map((e) => e.dataset.span));
  check(spans.includes(`${iso(monday)}T09:00/12:30`), `Monday morning missing: ${spans}`);
  check(spans.includes(`${iso(monday)}T13:30/17:00`), `Monday afternoon missing: ${spans}`);
  check(!spans.some((s) => s.startsWith(iso(tuesday))), `Tuesday should be off: ${spans}`);
  log("free time shows the lunch break and the day off");
} else {
  log("(free-time check skipped: next Monday is outside the 7-day preview)");
}

// Service that requires the booking field, performed by Eva.
await page.goto(`${WEB}/settings/services`);
const form = page.locator('[data-service-form="new"]');
await form.getByLabel("Name", { exact: true }).fill("Tyre change");
await form.getByLabel("Duration (minutes)").fill("45");
await form.getByLabel("Buffer after (minutes)").fill("15");
await form.getByLabel("Price (€)").fill("60");
await form.getByLabel("Licence plate").check();
await page.getByTestId("add-service").click();
const card = page.locator('[data-service="Tyre change"]');
await card.getByText(/45 min · €60\.00/).waitFor();
await card.getByText("Edit").click();
check(await card.getByLabel("Licence plate").isChecked(), "required field not saved");
check(await card.getByLabel("Eva").isChecked(), "Eva not linked");
log("service with a required booking field, performed by Eva");

await browser.close();
console.log("\nALL PASSED (setup)");
