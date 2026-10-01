// Webhook ledger + jobs through the real API and worker: duplicate delivery is a
// no-op, a failing event is retried, dead-lettered into System health, and can be
// retried by hand. Needs CONFLUO_WEBHOOK_TEST_SECRET (see .env.example).
import { createHmac } from "node:crypto";

import { chromium } from "playwright";

import { WEB, check, log, ownerWithBusiness } from "./helpers.mjs";

const API = "http://127.0.0.1:8100";
const SECRET = process.env.CONFLUO_WEBHOOK_TEST_SECRET;
check(SECRET, "CONFLUO_WEBHOOK_TEST_SECRET not set; run via `make e2e`");

async function sendWebhook(payload) {
  const body = JSON.stringify(payload);
  const sig = "sha256=" + createHmac("sha256", SECRET).update(body).digest("hex");
  const r = await fetch(`${API}/webhooks/test`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Confluo-Signature": sig },
    body,
  });
  if (!r.ok) throw new Error(`webhook: ${r.status} ${await r.text()}`);
  return (await r.json()).status;
}

const browser = await chromium.launch();
const { page } = await ownerWithBusiness(browser, "jobs");
const tenantId = await page.locator("header").getAttribute("data-tenant-id");
check(tenantId, "no tenant id in header");

await page.goto(`${WEB}/settings/system`);
await page.locator('[data-health="ok"]').waitFor();
log("System health starts empty");

const event = { id: `e2e-${Date.now()}`, tenant_id: tenantId, fail: "payment provider down" };
check((await sendWebhook(event)) === "accepted", "first delivery not accepted");
check((await sendWebhook(event)) === "duplicate", "redelivery not a duplicate");
log("a redelivered webhook is recognised as a duplicate");

// The worker retries with backoff, then dead-letters it.
let job;
for (let i = 0; i < 40 && !job; i++) {
  await page.waitForTimeout(1000);
  await page.reload();
  job = await page.locator("[data-job]").first().getAttribute("data-job").catch(() => null);
}
check(job, "failed job never showed up in System health");
const row = page.locator(`[data-job="${job}"]`);
await row.getByText("RuntimeError: payment provider down").waitFor();
await row.getByText(/3 attempts/).waitFor();
log("the failing event was retried 3 times, then listed in System health");

await row.getByRole("button", { name: "Retry" }).click();
await page.getByRole("status").getByText("Queued for another try.").waitFor();
log("manual retry queues the job again");

await page.goto(`${WEB}/settings/activity`);
await page.locator('[data-audit="retry job"]').waitFor();
log("the retry is in the audit log");

await browser.close();
console.log("\nALL PASSED (jobs)");
