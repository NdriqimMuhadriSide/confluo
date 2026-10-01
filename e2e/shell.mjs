// Dashboard shell: module nav after sign-in, language switch (en/nl/fr/de/sq) and the
// phone layout (menu sheet, no horizontal scrolling).
import { chromium } from "playwright";

import { WEB, check, log, ownerWithBusiness, signIn } from "./helpers.mjs";

const browser = await chromium.launch();
const { page, email, name } = await ownerWithBusiness(browser, "shell");
const aside = page.locator("aside nav");
for (const item of ["Home", "Inbox", "Calendar", "Customers", "Knowledge base", "Members", "Settings"]) {
  await aside.getByRole("link", { name: item, exact: true }).waitFor();
}
log("after sign-in the home page shows the module nav");

const expected = {
  nl: { nav: "Klanten", heading: `Welkom bij ${name}` },
  fr: { nav: "Clients", heading: `Bienvenue chez ${name}` },
  de: { nav: "Kunden", heading: `Willkommen bei ${name}` },
  sq: { nav: "Klientët", heading: `Mirë se vjen te ${name}` },
  en: { nav: "Customers", heading: `Welcome to ${name}` },
};
for (const [locale, { nav, heading }] of Object.entries(expected)) {
  await page.locator("header").getByTestId("language-switcher").selectOption(locale);
  await page.getByRole("heading", { name: heading }).waitFor();
  await aside.getByRole("link", { name: nav, exact: true }).waitFor();
  check((await page.locator("html").getAttribute("lang")) === locale, `html lang should be ${locale}`);
}
log("language switch works for en, nl, fr, de and sq (nav, headings, <html lang>)");

await page.locator("header").getByTestId("language-switcher").selectOption("nl");
await page.getByRole("heading", { name: expected.nl.heading }).waitFor();
const loginPage = await page.context().newPage();
await loginPage.goto(`${WEB}/settings/modules`);
await loginPage.getByRole("link", { name: "Activiteit" }).waitFor();
await loginPage.close();
await page.locator("header").getByTestId("language-switcher").selectOption("en");
await page.getByRole("heading", { name: expected.en.heading }).waitFor();
log("the choice sticks across pages (cookie)");

// --- Phone ---------------------------------------------------------------------------
const phone = await (await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true })).newPage();
await signIn(phone, email);
check(!(await phone.locator("aside").isVisible()), "sidebar should be hidden on phones");
await phone.getByRole("button", { name: "Menu" }).click();
const sheet = phone.getByRole("dialog");
await sheet.getByRole("link", { name: "Members" }).click();
await phone.getByRole("heading", { name: `Members of ${name}` }).waitFor();
await sheet.waitFor({ state: "detached" });
log("phone: the menu opens as a sheet, navigates and closes");

for (const path of [
  "/", "/members", "/settings/modules", "/settings/locations", "/settings/services", "/settings/team",
  "/settings/fields", "/settings/usage", "/settings/activity", "/settings/system", "/inbox",
]) {
  await phone.goto(`${WEB}${path}`);
  await phone.waitForLoadState("networkidle");
  const overflow = await phone.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  check(overflow <= 0, `${path} scrolls sideways by ${overflow}px on a phone`);
}
log("phone: no page scrolls sideways (11 pages)");

await page.screenshot({ path: process.env.SHOT_DESKTOP ?? "/tmp/confluo-desktop.png", fullPage: true });
await phone.goto(`${WEB}/members`);
await phone.screenshot({ path: process.env.SHOT_PHONE ?? "/tmp/confluo-phone.png", fullPage: true });

await browser.close();
console.log("\nALL PASSED (shell)");
