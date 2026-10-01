// End-to-end check of businesses, invitations, roles and the business switcher,
// against a running local stack (`make dev` or `make up`). Run with `make e2e`.
import { chromium } from "playwright";

const WEB = "http://localhost:3000";
const MAILPIT = "http://127.0.0.1:54324";
const SUPABASE = "http://127.0.0.1:54321";
// Secret key of the Supabase *local* stack (from `supabase status`, passed in by
// `make e2e`), used only to create the owner quickly.
const LOCAL_SECRET = process.env.SUPABASE_LOCAL_SECRET_KEY;
if (!LOCAL_SECRET) throw new Error("SUPABASE_LOCAL_SECRET_KEY not set; run via `make e2e`");
const run = Date.now();
const ownerEmail = `owner+${run}@example.com`;
const inviteeEmail = `invitee+${run}@example.com`;
const password = "correct-horse-42";
const shop = `Kapsalon ${run}`;
const log = (...a) => console.log("✓", ...a);

async function createConfirmedUser(email) {
  const r = await fetch(`${SUPABASE}/auth/v1/admin/users`, {
    method: "POST",
    headers: { apikey: LOCAL_SECRET, Authorization: `Bearer ${LOCAL_SECRET}`, "Content-Type": "application/json" },
    body: JSON.stringify({ email, password, email_confirm: true }),
  });
  if (!r.ok) throw new Error(`create user: ${r.status} ${await r.text()}`);
}

async function latestMail(to) {
  for (let i = 0; i < 30; i++) {
    const r = await fetch(`${MAILPIT}/api/v1/search?query=${encodeURIComponent(`to:"${to}"`)}`);
    const { messages } = await r.json();
    if (messages.length) {
      const full = await (await fetch(`${MAILPIT}/api/v1/message/${messages[0].ID}`)).json();
      const link = full.HTML.match(/href="([^"]+)"/)[1].replaceAll("&amp;", "&");
      return { subject: messages[0].Subject, link };
    }
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error(`no email for ${to}`);
}

async function signIn(page, email) {
  await page.goto(`${WEB}/login`);
  await page.fill("#email", email);
  await page.fill("#password", password);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await page.waitForURL(`${WEB}/`);
}

const browser = await chromium.launch();

// --- Owner sets up a business -------------------------------------------------------
await createConfirmedUser(ownerEmail);
const owner = await (await browser.newContext()).newPage();
await signIn(owner, ownerEmail);
await owner.getByText("Set up your business").waitFor();
await owner.fill("#name", shop);
await owner.getByRole("button", { name: "Create business" }).click();
await owner.getByRole("heading", { name: new RegExp(`${shop}.*owner`) }).waitFor();
log("new user creates a business and is its owner");

// --- Owner invites a new person as staff ----------------------------------------------
await owner.getByRole("link", { name: "Members" }).click();
await owner.fill("#invite-email", inviteeEmail);
await owner.selectOption("#invite-role", "staff");
await owner.getByRole("button", { name: "Invite" }).click();
await owner.getByText(`Invited ${inviteeEmail} as staff. We emailed them an invitation.`).waitFor();
await owner.getByText(`${inviteeEmail} · staff · pending`).waitFor();
log("owner invites by email; invitation listed as pending");

const mail = await latestMail(inviteeEmail);
if (!mail.subject.includes(shop)) throw new Error(`unexpected subject: ${mail.subject}`);
log(`invitation email received: "${mail.subject}"`);

// --- Invitee follows the link, accepts, and is staff -----------------------------------
const invitee = await (await browser.newContext()).newPage();
await invitee.goto(mail.link);
await invitee.waitForURL(`${WEB}/`);
await invitee.getByText(`Join ${shop} as staff`).waitFor();
await invitee.getByRole("button", { name: "Accept" }).click();
await invitee.getByRole("heading", { name: new RegExp(`${shop}.*staff`) }).waitFor();
log("invite link signs the invitee in; they accept and join as staff");

await invitee.getByRole("link", { name: "Members" }).click();
await invitee.locator(`[data-member="${ownerEmail}"]`).waitFor();
if (await invitee.getByText("Invite someone").count()) throw new Error("staff sees invite form");
if (await invitee.getByRole("button", { name: "Remove" }).count()) throw new Error("staff sees remove");
log("staff sees the team but no invite / role / remove controls");

// --- Owner manages roles; last owner is protected --------------------------------------
await owner.reload();
const row = owner.locator(`[data-member="${inviteeEmail}"]`);
await row.waitFor();
if (await owner.getByText(`${inviteeEmail} · staff · pending`).count()) throw new Error("invite still pending");
await row.locator("select").selectOption("admin");
await row.getByRole("button", { name: "Save" }).click();
await owner.waitForURL(`${WEB}/members`);
await owner.reload();
const saved = await owner.locator(`[data-member="${inviteeEmail}"] select`).inputValue();
if (saved !== "admin") throw new Error(`role after save: ${saved}`);
log("owner promotes the member to admin");

const ownerRow = owner.locator(`[data-member="${ownerEmail}"]`);
await ownerRow.getByRole("button", { name: "Remove" }).click();
await owner.getByRole("alert").getByText("A business needs at least one owner").waitFor();
log("removing the last owner is refused with a clear message");

// --- Admin now sees management controls, but not for the owner -------------------------
await invitee.reload();
await invitee.getByText("Invite someone").waitFor();
if (await invitee.locator(`[data-member="${ownerEmail}"] select`).count()) throw new Error("admin can edit owner");
if (await invitee.locator("#invite-role option[value=owner]").count()) throw new Error("admin can invite owners");
log("admin gets invite controls, but can't edit the owner or invite owners");

// --- Switching between businesses -------------------------------------------------------
await owner.goto(`${WEB}/`);
await owner.getByText("Add another business").waitFor();
await owner.fill("#name", `Second ${run}`);
await owner.getByRole("button", { name: "Create business" }).click();
await owner.getByRole("heading", { name: new RegExp(`Second ${run}`) }).waitFor();
const switcher = owner.locator("header select");
if ((await switcher.locator("option").count()) !== 2) throw new Error("switcher should list 2 businesses");
await switcher.selectOption({ label: shop });
await owner.getByRole("heading", { name: new RegExp(`${shop}.*owner`) }).waitFor();
await owner.getByRole("link", { name: "Members" }).click();
await owner.getByRole("heading", { name: `Members of ${shop}` }).waitFor();
// The owner belongs to two businesses; only this one's members may be listed.
const rows = await owner.locator("[data-member]").count();
if (rows !== 2) throw new Error(`expected 2 members of ${shop}, got ${rows}`);
log("owner creates a second business and switches between them; members follow the switch");

// The invitee only belongs to the first business.
await invitee.goto(`${WEB}/`);
if ((await invitee.locator("header select option").count()) !== 1) throw new Error("invitee sees other business");
log("the second business is invisible to the invitee");

// --- Inviting an existing account: no email, shown at sign-in ---------------------------
await owner.goto(`${WEB}/`);
await owner.locator("header select").selectOption({ label: `Second ${run}` });
await owner.getByRole("heading", { name: new RegExp(`Second ${run}`) }).waitFor();
await owner.getByRole("link", { name: "Members" }).click();
await owner.fill("#invite-email", inviteeEmail);
await owner.getByRole("button", { name: "Invite" }).click();
await owner.getByText("They'll see the invitation next time they sign in.").waitFor();
await invitee.reload();
await invitee.getByText(`Join Second ${run} as staff`).waitFor();
log("existing account invited: no email, invitation shows in their dashboard");

await browser.close();
console.log("\nALL PASSED (rbac) for", ownerEmail);
