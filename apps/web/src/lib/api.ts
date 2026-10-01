// Server-side base URL of the Confluo API. The generated OpenAPI client replaces
// hand-written calls in the CI card.
export const API_URL = process.env.CONFLUO_API_URL ?? "http://127.0.0.1:8100";

export type Health = { status: "ok" | "degraded"; database: boolean };

export async function getHealth(): Promise<Health | null> {
  try {
    const res = await fetch(`${API_URL}/health`, { cache: "no-store" });
    return res.ok ? ((await res.json()) as Health) : null;
  } catch {
    return null;
  }
}
