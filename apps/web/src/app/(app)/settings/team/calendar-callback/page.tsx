import { redirect } from "next/navigation";
import { getTranslations } from "next-intl/server";

import { apiClient, inTenant } from "@/lib/api/client";
import { requireTenant } from "@/lib/session";

// Microsoft sends the browser back here after sign-in (?code&state, or ?error).
// The API checks the state (same business, same person, not expired) and stores
// the tokens; then we return to the staff member's page.
export default async function CalendarCallback({ searchParams }: PageProps<"/settings/team/calendar-callback">) {
  const params = await searchParams;
  const t = await getTranslations("setup.calendar");
  const { tenantId } = await requireTenant();
  const code = typeof params.code === "string" ? params.code : null;
  const state = typeof params.state === "string" ? params.state : null;
  if (!code || !state) redirect(`/settings/team?error=${encodeURIComponent(t("signInFailed"))}`);
  const { data, error } = await (await apiClient()).POST("/api/crm/calendar/microsoft/callback", {
    params: { header: inTenant(tenantId) },
    body: { code, state },
  });
  if (!data) {
    const detail = (error as { detail?: unknown } | undefined)?.detail;
    redirect(`/settings/team?error=${encodeURIComponent(typeof detail === "string" ? detail : t("signInFailed"))}`);
  }
  redirect(`/settings/team/${data.resource_id}?saved=1`);
}
