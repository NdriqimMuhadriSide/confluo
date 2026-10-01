import createClient from "openapi-fetch";

import { getAccessToken } from "@/lib/supabase/server";

import type { components, paths } from "./schema";

// Server-side base URL of the Confluo API.
export const API_URL = process.env.CONFLUO_API_URL || "http://127.0.0.1:8100";

export type Health = components["schemas"]["Health"];
export type Me = components["schemas"]["Me"];

// Typed client generated from apps/web/openapi.json (`make api-client`). Sends the
// signed-in user's Supabase access token, so the API can verify who is calling.
export async function apiClient() {
  const token = await getAccessToken();
  return createClient<paths>({
    baseUrl: API_URL,
    cache: "no-store",
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
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
