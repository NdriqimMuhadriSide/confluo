"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";

import { apiClient, errorMessage, inTenant, type Schemas } from "@/lib/api/client";

const str = (form: FormData, key: string) => String(form.get(key) ?? "").trim();

function done(error?: unknown): never {
  revalidatePath("/knowledge");
  redirect(error ? `/knowledge?error=${encodeURIComponent(errorMessage(error, "Could not save."))}` : "/knowledge?saved=1");
}

function item(form: FormData): Schemas["ItemIn"] {
  return {
    kind: str(form, "kind") as Schemas["ItemIn"]["kind"],
    language: str(form, "language") as Schemas["ItemIn"]["language"],
    title: str(form, "title"),
    body: str(form, "body"),
  };
}

export async function saveItem(form: FormData): Promise<void> {
  const header = inTenant(str(form, "tenant_id"));
  const id = str(form, "id");
  const api = await apiClient();
  const { error } = id
    ? await api.PUT("/api/crm/knowledge/{item_id}", { params: { header, path: { item_id: id } }, body: item(form) })
    : await api.POST("/api/crm/knowledge", { params: { header }, body: item(form) });
  done(error);
}

export async function setPublished(form: FormData): Promise<void> {
  const params = { header: inTenant(str(form, "tenant_id")), path: { item_id: str(form, "id") } };
  const api = await apiClient();
  const { error } =
    form.get("publish") === "true"
      ? await api.POST("/api/crm/knowledge/{item_id}/publish", { params })
      : await api.POST("/api/crm/knowledge/{item_id}/unpublish", { params });
  done(error);
}

export async function deleteItem(form: FormData): Promise<void> {
  const { error } = await (await apiClient()).DELETE("/api/crm/knowledge/{item_id}", {
    params: { header: inTenant(str(form, "tenant_id")), path: { item_id: str(form, "id") } },
  });
  done(error);
}
