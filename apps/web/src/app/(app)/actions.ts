"use server";

import { revalidatePath } from "next/cache";
import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { getTranslations } from "next-intl/server";

import { apiClient, errorMessage, inTenant, TENANT_COOKIE } from "@/lib/api/client";

export type FormState = { error?: string; message?: string };

const ONE_YEAR = 60 * 60 * 24 * 365;

async function chooseTenant(id: string) {
  (await cookies()).set(TENANT_COOKIE, id, {
    httpOnly: true,
    sameSite: "lax",
    path: "/",
    maxAge: ONE_YEAR,
  });
}

export async function switchTenant(form: FormData): Promise<void> {
  await chooseTenant(String(form.get("tenant_id")));
  revalidatePath("/", "layout");
}

export async function createTenant(_: FormState, form: FormData): Promise<FormState> {
  const name = String(form.get("name") ?? "").trim();
  const slug = String(form.get("slug") ?? "").trim().toLowerCase();
  const t = await getTranslations("home");
  const { data, response } = await (await apiClient()).POST("/api/tenants", { body: { name, slug } });
  if (!data) return { error: response.status === 409 ? t("slugTaken") : t("slugInvalid") };
  await chooseTenant(data.id);
  revalidatePath("/", "layout");
  return { message: t("created", { name: data.name }) };
}

export async function acceptInvitation(form: FormData): Promise<void> {
  const id = String(form.get("invitation_id"));
  const { data } = await (await apiClient()).POST("/api/me/invitations/{invitation_id}/accept", {
    params: { path: { invitation_id: id } },
  });
  if (data) await chooseTenant(data.id);
  revalidatePath("/", "layout");
}

export async function inviteMember(_: FormState, form: FormData): Promise<FormState> {
  const tenantId = String(form.get("tenant_id"));
  const email = String(form.get("email") ?? "").trim();
  const role = String(form.get("role")) as "owner" | "admin" | "staff";
  const { data, error, response } = await (await apiClient()).POST("/api/invitations", {
    params: { header: inTenant(tenantId) },
    body: { email, role },
  });
  const t = await getTranslations();
  if (!data) {
    return { error: response.status === 422 ? t("members.invalidEmail") : errorMessage(error, t("members.invalidEmail")) };
  }
  revalidatePath("/members");
  const note = data.email_outcome === "email_sent" ? t("members.emailSent") : t("members.atSignIn");
  const roleName = t(`common.roles.${data.role}`);
  return { message: `${t("members.invited", { email: data.email, role: roleName })} ${note}` };
}

// Row actions report errors (e.g. "needs at least one owner") through the URL, so
// the members page can stay a server component.
function backToMembers(error?: unknown): never {
  revalidatePath("/", "layout");
  redirect(error ? `/members?error=${encodeURIComponent(errorMessage(error, "That didn't work."))}` : "/members");
}

export async function changeRole(form: FormData): Promise<void> {
  const tenantId = String(form.get("tenant_id"));
  const { error } = await (await apiClient()).PATCH("/api/members/{user_id}", {
    params: { header: inTenant(tenantId), path: { user_id: String(form.get("user_id")) } },
    body: { role: String(form.get("role")) as "owner" | "admin" | "staff" },
  });
  backToMembers(error);
}

export async function removeMember(form: FormData): Promise<void> {
  const tenantId = String(form.get("tenant_id"));
  const { error } = await (await apiClient()).DELETE("/api/members/{user_id}", {
    params: { header: inTenant(tenantId), path: { user_id: String(form.get("user_id")) } },
  });
  backToMembers(error);
}

export async function revokeInvitation(form: FormData): Promise<void> {
  const tenantId = String(form.get("tenant_id"));
  await (await apiClient()).DELETE("/api/invitations/{invitation_id}", {
    params: {
      header: inTenant(tenantId),
      path: { invitation_id: String(form.get("invitation_id")) },
    },
  });
  revalidatePath("/members");
}
