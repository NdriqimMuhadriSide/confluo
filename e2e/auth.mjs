// End-to-end check of staff auth against a running local stack (`make dev` or `make up`):
// sign-up + email confirmation, password sign-in/out, magic link, and the refusals.
// Emails are read from Mailpit. Run with `make e2e`.
import { chromium } from "playwright";

const WEB = "http://localhost:3000";
const MAILPIT = "http://127.0.0.1:54324";
const email = `staff+${Date.now()}@example.com`;
const password = "correct-horse-42";
const log = (...a) => console.log("✓", ...a);

async function latestLink(to, subjectHint) {
  for (let i = 0; i < 30; i++) {
    const r = await fetch(`${MAILPIT}/api/v1/search?query=${encodeURIComponent(`to:"${to}"`)}`);
    const { messages } = await r.json();
    const m = messages.find((m) => !subjectHint || m.Subject.toLowerCase().includes(subjectHint));
    if (m) {
      const full = await (await fetch(`${MAILPIT}/api/v1/message/${m.ID}`)).json();
      await fetch(`${MAILPIT}/api/v1/messages`, { method: "DELETE", body: JSON.stringify({ IDs: [m.ID] }) });
      const link = full.HTML.match(/href="([^"]+)"/)[1].replaceAll("&amp;", "&");
      return { subject: m.Subject, link };
    }
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error(`no email for ${to}`);
}

const browser = await chromium.launch();
const page = await browser.newPage();

// 1. Signed-out visit redirects to /login
await page.goto(`${WEB}/`);
if (page.url() !== `${WEB}/login`) throw new Error(`expected /login, got ${page.url()}`);
log("signed-out visit to / lands on /login");

// 2. Sign up → confirmation email
await page.goto(`${WEB}/signup`);
await page.fill("#name", "Anna Test");
await page.fill("#email", email);
await page.fill("#password", password);
await page.click("button[type=submit]");
await page.getByText("Check your email to confirm").waitFor();
log("sign-up asks to confirm email");

// 3. Unconfirmed account can't sign in yet
await page.goto(`${WEB}/login`);
await page.fill("#email", email);
await page.fill("#password", password);
await page.getByRole("button", { name: "Sign in", exact: true }).click();
await page.getByText("Wrong email or password").waitFor();
log("unconfirmed account is refused");

// 4. Click confirmation link → signed in on /
const confirm = await latestLink(email);
log(`confirmation email received: "${confirm.subject}"`);
await page.goto(confirm.link);
await page.waitForURL(`${WEB}/`);
await page.getByText(`Signed in as ${email}`).waitFor();
log("confirmation link signs the user in");

async function assertApiAcceptsSession() {
  const row = page.locator("div", { hasText: /^API accepts your session/ }).last();
  const text = await row.innerText();
  if (!text.includes("up")) throw new Error(`API did not accept session: ${text}`);
}
await assertApiAcceptsSession();
log("API /api/me accepts the session token");

// 5. Sign out → back to /login, / is protected again
await page.getByRole("button", { name: "Sign out" }).click();
await page.waitForURL(`${WEB}/login`);
await page.goto(`${WEB}/`);
if (!page.url().endsWith("/login")) throw new Error("still signed in after sign out");
log("sign out works and / is protected again");

// 6. Wrong password
await page.fill("#email", email);
await page.fill("#password", "wrong-password");
await page.getByRole("button", { name: "Sign in", exact: true }).click();
await page.getByText("Wrong email or password").waitFor();
log("wrong password is refused");

// 7. Password sign-in
await page.fill("#password", password);
await page.getByRole("button", { name: "Sign in", exact: true }).click();
await page.waitForURL(`${WEB}/`);
await page.getByText(`Signed in as ${email}`).waitFor();
await assertApiAcceptsSession();
log("password sign-in works");

// 8. Magic link sign-in (in a fresh browser context)
const ctx2 = await browser.newContext();
const p2 = await ctx2.newPage();
await p2.goto(`${WEB}/login`);
await p2.fill("#email", email);
await p2.getByRole("button", { name: "Email me a sign-in link" }).click();
await p2.getByText("a sign-in link is on its way").waitFor();
const magic = await latestLink(email);
log(`magic link email received: "${magic.subject}"`);
await p2.goto(magic.link);
await p2.waitForURL(`${WEB}/`);
await p2.getByText(`Signed in as ${email}`).waitFor();
log("magic link signs the user in");

// 9. Magic link for an unknown email gives the same neutral answer, sends nothing
await p2.context().clearCookies();
await p2.goto(`${WEB}/login`);
const ghost = `nobody+${Date.now()}@example.com`;
await p2.fill("#email", ghost);
await p2.getByRole("button", { name: "Email me a sign-in link" }).click();
await p2.getByText("a sign-in link is on its way").waitFor();
await new Promise((r) => setTimeout(r, 1500));
const r = await (await fetch(`${MAILPIT}/api/v1/search?query=${encodeURIComponent(`to:"${ghost}"`)}`)).json();
if (r.messages.length) throw new Error("email sent to unknown address");
log("unknown email: neutral message, no email sent, no account created");

// 10. Reused magic link is rejected
await p2.goto(magic.link);
await p2.waitForURL(/\/login/);
await p2.getByText("invalid or has expired").waitFor();
log("reused magic link is rejected");

await browser.close();
console.log("\nALL PASSED for", email);
