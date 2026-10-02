// The demo story end to end, on the seeded demo salon (`make seed`; approval mode):
// a visitor books through the chat on the salon's website, staff approve it in the
// calendar, and the confirmation appears in the visitor's chat. Runs with the
// offline stand-in brain (local default) or with Claude; it checks outcomes, not
// wording.
import { chromium } from "playwright";

import { check, log, WEB } from "./helpers.mjs";

const SITE = "http://127.0.0.1:3001/salon.html";
// A unique surname of letters only (names are words to the offline brain).
const name = `Lotte ${"Test" + [...String(Date.now() % 1e6)].map((d) => "abcdefghij"[d]).join("")}`;
const browser = await chromium.launch();
const ctx = await browser.newContext({ locale: "en-GB" });

const site = await ctx.newPage();
await site.goto(SITE);
await site.getByRole("button", { name: "Maak een afspraak" }).click();
const them = site.locator(".msg.them");
async function say(text) {
  const before = await them.count();
  await site.getByRole("textbox").fill(text);
  await site.getByRole("textbox").press("Enter");
  for (let i = 0; i < 120 && (await them.count()) <= before; i++) await site.waitForTimeout(250);
  check((await them.count()) > before, `no reply to "${text}"`);
  return (await them.last().textContent()) ?? "";
}
await say("Hi! I'd like to book a women's cut");
const offer = await say("next friday");
check(/1\)/.test(offer), `expected numbered times, got: ${offer}`);
log("the AI offers free times");
let reply = await say("2");
for (const answer of [`my name is ${name}`, "0470 12 34 56", "medium"]) {
  if (/shall i book/i.test(reply)) break;
  reply = await say(answer);
}
check(/shall i book/i.test(reply), `expected a summary to confirm, got: ${reply}`);
await say("yes");
log("booked through the chat after an explicit yes");

const dash = await ctx.newPage();
await dash.goto(`${WEB}/login`);
await dash.fill("#email", "demo@confluo.local");
await dash.fill("#password", "demo-confluo-2026");
await dash.getByTestId("sign-in").click();
await dash.waitForURL(`${WEB}/`);
const friday = new Date();
friday.setDate(friday.getDate() + (((5 - friday.getDay() + 7) % 7) || 7));
const week = new Date(friday);
week.setDate(week.getDate() - 4);
const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
await dash.goto(`${WEB}/calendar?week=${iso(week)}`);
const pending = dash.locator(`[data-pending="${name}"]`);
await pending.waitFor();
log("the request waits for approval in the calendar");
await pending.getByRole("button", { name: "Approve" }).click();
await dash.locator(`[data-appointment="${name}"][data-status="confirmed"]`).waitFor();
for (let i = 0; i < 40 && !/confirmed/i.test((await them.last().textContent()) ?? ""); i++) await site.waitForTimeout(250);
check(/confirmed/i.test((await them.last().textContent()) ?? ""), "no confirmation in the chat");
log("approved; the visitor sees the confirmation live");

await dash.goto(`${WEB}/inbox`);
await dash.locator(`[data-conversation="${name}"]`).first().click();
await dash.locator('[data-from="system"]').last().waitFor();
log("the conversation is in the inbox");
await browser.close();
