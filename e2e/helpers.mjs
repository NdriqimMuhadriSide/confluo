// Shared helpers for the browser tests. They run against a local stack started with
// `make dev` (or `make up`); `make e2e` passes the local Supabase secret key.
export const WEB = "http://localhost:3000";
export const MAILPIT = "http://127.0.0.1:54324";
export const SUPABASE = "http://127.0.0.1:54321";
export const PASSWORD = "correct-horse-42";
export const log = (...a) => console.log("✓", ...a);

export function check(condition, message) {
  if (!condition) throw new Error(message);
}

export async function createConfirmedUser(email) {
  const key = process.env.SUPABASE_LOCAL_SECRET_KEY;
  check(key, "SUPABASE_LOCAL_SECRET_KEY not set; run via `make e2e`");
  const r = await fetch(`${SUPABASE}/auth/v1/admin/users`, {
    method: "POST",
    headers: { apikey: key, Authorization: `Bearer ${key}`, "Content-Type": "application/json" },
    body: JSON.stringify({ email, password: PASSWORD, email_confirm: true }),
  });
  if (!r.ok) throw new Error(`create user: ${r.status} ${await r.text()}`);
}

export async function signIn(page, email) {
  await page.goto(`${WEB}/login`);
  await page.fill("#email", email);
  await page.fill("#password", PASSWORD);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await page.waitForURL(`${WEB}/`);
}

// A signed-in owner of a fresh business.
export async function ownerWithBusiness(browser, label) {
  const email = `${label}+${Date.now()}@example.com`;
  await createConfirmedUser(email);
  const page = await (await browser.newContext()).newPage();
  await signIn(page, email);
  const name = `${label} ${Date.now()}`;
  await page.fill("#name", name);
  await page.getByRole("button", { name: "Create business" }).click();
  await page.getByTestId("business-heading").getByText(name).waitFor();
  return { page, email, name };
}
