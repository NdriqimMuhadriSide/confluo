import createClient from "openapi-fetch";
import { cookies } from "next/headers";

import { getAccessToken } from "@/lib/supabase/server";

import type { components, paths } from "./schema";

// Server-side base URL of the Confluo API.
export const API_URL = process.env.CONFLUO_API_URL || "http://127.0.0.1:8100";

// Cookie holding the business the user last switched to.
export const TENANT_COOKIE = "confluo_tenant";

export type Schemas = components["schemas"];
export type Health = Schemas["Health"];
export type Me = Schemas["Me"];

// Typed client generated from apps/web/openapi.json (`make api-client`). Sends the
// signed-in user's Supabase access token. Tenant-scoped endpoints declare the
// X-Tenant-Id header, so the types make every such call pass `inTenant(id)`.
export async function apiClient() {
  const token = await getAccessToken();
  return createClient<paths>({
    baseUrl: API_URL,
    cache: "no-store",
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
}

export function inTenant(tenantId: string) {
  return { "X-Tenant-Id": tenantId };
}

export async function getHealth(): Promise<Health | null> {
  try {
    const { data } = await (await apiClient()).GET("/health");
    return data ?? null;
  } catch {
    return null;
  }
}

export async function getMe(): Promise<Me | null> {
  try {
    const { data } = await (await apiClient()).GET("/api/me");
    return data ?? null;
  } catch {
    return null;
  }
}

// The tenant to act in: the one in the cookie if the user still belongs to it,
// otherwise their first. null when they belong to none yet.
export async function currentTenantId(me: Me): Promise<string | null> {
  const chosen = (await cookies()).get(TENANT_COOKIE)?.value;
  if (chosen && me.tenants.some((t) => t.id === chosen)) return chosen;
  return me.tenants[0]?.id ?? null;
}

// FastAPI error bodies are {detail: string} or a validation list.
export function errorMessage(error: unknown, fallback: string): string {
  const detail = (error as { detail?: unknown } | undefined)?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return fallback;
  return fallback;
}

export type Manifest = Schemas["Manifest"];

export async function getManifest(tenantId: string): Promise<Manifest> {
  const { data } = await (await apiClient()).GET("/api/me/manifest", {
    params: { header: inTenant(tenantId) },
  });
  return data ?? { modules: [], nav: [], permissions: [] };
}
