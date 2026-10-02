"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";

import { apiClient, inTenant } from "@/lib/api/client";

const str = (form: FormData, key: string) => String(form.get(key) ?? "").trim();

function back(path: string, error?: unknown): never {
  revalidatePath(path);
  const detail = (error as { detail?: unknown } | undefined)?.detail;
  const message = typeof detail === "string" ? detail : error ? "Could not save." : null;
  redirect(message ? `${path}${path.includes("?") ? "&" : "?"}error=${encodeURIComponent(message)}` : path);
}

// --- Calendar ------------------------------------------------------------------------------

export async function setAppointmentStatus(form: FormData): Promise<void> {
  const action = str(form, "action") as "approve" | "reject" | "cancel";
  const path = `/api/crm/appointments/{appointment_id}/${action}` as const;
  const { error } = await (await apiClient()).POST(path, {
    params: { header: inTenant(str(form, "tenant_id")), path: { appointment_id: str(form, "id") } },
  });
  back(str(form, "back") || "/calendar", error);
}

export async function createAppointment(form: FormData): Promise<void> {
  const { error } = await (await apiClient()).POST("/api/crm/appointments", {
    params: { header: inTenant(str(form, "tenant_id")) },
    body: {
      service_id: str(form, "service_id"),
      resource_id: str(form, "resource_id"),
      start: `${str(form, "date")}T${str(form, "time")}:00`,
      customer_name: str(form, "customer_name"),
    },
  });
  back(str(form, "back") || "/calendar", error);
}

// --- Inbox ---------------------------------------------------------------------------------

export async function replyToConversation(form: FormData): Promise<void> {
  const id = str(form, "id");
  const { error } = await (await apiClient()).POST("/api/crm/conversations/{conversation_id}/messages", {
    params: { header: inTenant(str(form, "tenant_id")), path: { conversation_id: id } },
    body: { text: str(form, "text") },
  });
  back(`/inbox?c=${id}`, error);
}

export async function handBack(form: FormData): Promise<void> {
  const id = str(form, "id");
  const { error } = await (await apiClient()).POST("/api/crm/conversations/{conversation_id}/handback", {
    params: { header: inTenant(str(form, "tenant_id")), path: { conversation_id: id } },
  });
  back(`/inbox?c=${id}`, error);
}
