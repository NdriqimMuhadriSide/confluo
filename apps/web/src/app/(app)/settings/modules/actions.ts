"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";

import { apiClient, errorMessage, inTenant } from "@/lib/api/client";

type Detail = { loc?: (string | number)[]; msg?: string }[];

function describe(error: unknown): string {
  const detail = (error as { detail?: unknown } | undefined)?.detail;
  if (Array.isArray(detail)) {
    return (detail as Detail).map((d) => `${(d.loc ?? []).slice(1).join(".")}: ${d.msg}`).join("; ");
  }
  return errorMessage(error, "Could not save.");
}

async function save(tenantId: string, key: string, body: { enabled?: boolean; config?: Record<string, unknown> }) {
  const { error } = await (await apiClient()).PUT("/api/modules/{key}", {
    params: { header: inTenant(tenantId), path: { key } },
    body,
  });
  revalidatePath("/", "layout");
  redirect(error ? `/settings/modules?error=${encodeURIComponent(describe(error))}` : "/settings/modules?saved=1");
}

export async function toggleModule(form: FormData): Promise<void> {
  await save(String(form.get("tenant_id")), String(form.get("key")), {
    enabled: form.get("enabled") === "true",
  });
}

// Form fields arrive as strings; convert them using the JSON schema types sent along.
export async function saveConfig(form: FormData): Promise<void> {
  const types = JSON.parse(String(form.get("__types"))) as Record<string, string>;
  const config: Record<string, unknown> = {};
  for (const [field, type] of Object.entries(types)) {
    const raw = form.get(field);
    if (type === "boolean") config[field] = raw === "on";
    else if (type === "integer" || type === "number") config[field] = raw === "" || raw === null ? null : Number(raw);
    else config[field] = raw;
  }
  await save(String(form.get("tenant_id")), String(form.get("key")), { config });
}
