import createClient from "openapi-fetch";

import type { components, paths } from "./schema";

// Server-side base URL of the Confluo API.
export const API_URL = process.env.CONFLUO_API_URL ?? "http://127.0.0.1:8100";

// Typed client generated from apps/web/openapi.json (`make api-client`).
export const api = createClient<paths>({ baseUrl: API_URL, cache: "no-store" });

export type Health = components["schemas"]["Health"];

export async function getHealth(): Promise<Health | null> {
  try {
    const { data } = await api.GET("/health");
    return data ?? null;
  } catch {
    return null;
  }
}
